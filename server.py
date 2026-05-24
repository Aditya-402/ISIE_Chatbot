"""EV Lab dashboard - FastAPI backend.

Serves the static frontend (frontend/) and exposes:
  GET  /                      single-page app
  GET  /api/state             full vehicle state snapshot
  POST /api/control/{ch}      drive a control channel (toggle/press/release/set)
  POST /api/ask               RAG question -> answer + sources
  POST /api/transcribe        raw PCM16 (16 kHz mono) -> text
  WS   /ws/state              pushes state deltas + blink mirror ticks

RAGEngine warms up on app startup so the first /api/ask is fast.
Hardware calls go through hardware.py (SIM on Windows, REAL on Pi).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import hardware
import voice
import bank_html
from rag_core import RAGEngine


log = logging.getLogger("ev_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# --- Vehicle state model -----------------------------------------------

class VehicleState:
    """Authoritative source of truth for control state. Mirrors hardware.py."""

    def __init__(self):
        self.state: dict[str, bool] = {ch: False for ch in config.CONTROL_CHANNELS}

    def snapshot(self) -> dict:
        return {
            "channels": dict(self.state),
            "ignition": self.state["ignition"],
        }

    def apply(self, ch: str, value: bool):
        self.state[ch] = value


vstate = VehicleState()


# --- WebSocket hub -----------------------------------------------------

class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def add(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)
        await ws.send_json({"type": "snapshot", **vstate.snapshot()})

    async def drop(self, ws: WebSocket):
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, message: dict):
        if not self.clients:
            return
        payload = json.dumps(message)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.clients.discard(ws)

    def push_from_thread(self, message: dict):
        """Schedule a broadcast from a worker thread (timed-pulse / self-test)."""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)


hub = Hub()


# --- Control orchestration ---------------------------------------------
# Two kinds of channels:
#   * physical GPIO outputs : ignition, headlight, horn, left_ind, right_ind, brake
#   * software-only signals : hazard, all_lamp
# ignition is special — it's a real output AND the master gate: every other
# output is forced off while it's off.
# Each action updates the *intent* in vstate, then _apply_outputs() derives the
# effective pin states (software modes can override individual intents) and
# drives hardware. Keeping the mode logic in one place avoids ordering bugs.

# Driven physical outputs that the ignition gate switches off (ignition itself
# is handled separately so it can stay lit as the gate).
_PIN_CHANNELS = ("headlight", "horn", "left_ind", "right_ind", "brake", "reverse")
_INDICATORS = ("left_ind", "right_ind")

# Auto-off timers for timed-pulse channels (reverse/forward).
_timers: dict[str, "threading.Timer"] = {}

# Power-on self-test timers (brake pulse + parking-brake tell-tale flash).
_brake_test_timers: list = []


def _cancel_brake_test():
    for t in _brake_test_timers:
        t.cancel()
    _brake_test_timers.clear()


def _schedule_brake_test():
    """After ignition ON: wait BRAKE_TEST_DELAY_S, then drive the brake output
    for BRAKE_TEST_HOLD_S while lighting the PARKING-BRAKE tell-tale (not the
    brake tell-tale). Brake is driven directly (we don't set vstate['brake'])
    so the brake indicator stays dark; the parking-brake indicator shows."""
    _cancel_brake_test()

    def _on():
        if not vstate.state["ignition"]:
            return
        hardware.set("brake", True)
        vstate.apply("parking_brake", True)
        hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})

    def _off():
        hardware.set("brake", False)
        vstate.apply("parking_brake", False)
        hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})

    t_on = threading.Timer(config.BRAKE_TEST_DELAY_S, _on)
    t_off = threading.Timer(config.BRAKE_TEST_DELAY_S + config.BRAKE_TEST_HOLD_S, _off)
    for t in (t_on, t_off):
        t.daemon = True
        _brake_test_timers.append(t)
        t.start()


def _start_timed_pulse(ch: str, seconds: float):
    """Drive `ch` ON now, then auto-OFF after `seconds`. Re-keying restarts the
    timer. Used for reverse/forward. Updates UI (vstate + broadcast) at both
    edges so the tell-tale lights for the duration."""
    old = _timers.pop(ch, None)
    if old:
        old.cancel()

    vstate.apply(ch, True)
    _apply_outputs()
    hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})

    def _expire():
        _timers.pop(ch, None)
        vstate.apply(ch, False)
        _apply_outputs()
        hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})

    t = threading.Timer(seconds, _expire)
    t.daemon = True
    _timers[ch] = t
    t.start()


def drive(ch: str, action: str) -> dict:
    """Apply a control action, recompute outputs, broadcast state."""
    if ch not in config.CONTROL_CHANNELS:
        raise HTTPException(404, f"unknown channel '{ch}'")

    # Ignition is the master GATE — it has no GPIO pin of its own.
    if ch == "ignition" and action in ("toggle", "press"):
        new = not vstate.state["ignition"]
        vstate.apply("ignition", new)
        if not new:
            # Killing ignition clears every other intent (modes included) and
            # cancels any pending timed pulses / self-test.
            for other in config.CONTROL_CHANNELS:
                if other != "ignition":
                    vstate.apply(other, False)
            for t in _timers.values():
                t.cancel()
            _timers.clear()
            _cancel_brake_test()
        _apply_outputs()
        hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})
        if new:
            _schedule_brake_test()    # power-on brake/parking self-test
        return {"channel": ch, "on": new, **vstate.snapshot()}

    if not vstate.state["ignition"]:
        # Ignition gate: ignore everything else until ignition is on.
        return {"channel": ch, "on": False, "ignored": True, **vstate.snapshot()}

    # Horn: a click fires a fixed beep pattern then auto-offs (not a latch).
    if ch == "horn":
        if action in ("press", "toggle"):
            hardware.beep("horn", config.HORN_BEEP_ON_S,
                          config.HORN_BEEP_GAP_S, config.HORN_BEEP_COUNT)
        vstate.apply("horn", False)
        hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})
        return {"channel": ch, "on": False, **vstate.snapshot()}

    # Reverse/forward: keying it on drives the pin for a fixed time, then auto-off.
    if ch == "reverse":
        if action in ("press", "toggle"):
            _start_timed_pulse("reverse", config.REVERSE_PULSE_S)
        return {"channel": ch, "on": vstate.state["reverse"], **vstate.snapshot()}

    if action == "toggle":
        new = not vstate.state[ch]
        vstate.apply(ch, new)
        # L/R turn signals are mutually exclusive when toggled directly.
        if ch in _INDICATORS and new:
            other = "right_ind" if ch == "left_ind" else "left_ind"
            vstate.apply(other, False)
    elif action == "press":
        vstate.apply(ch, True)
    elif action == "release":
        vstate.apply(ch, False)
    elif action == "set":
        raise HTTPException(400, "use POST body with action=set and value")
    else:
        raise HTTPException(400, f"unknown action '{action}'")

    _apply_outputs()
    hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})
    return {"channel": ch, "on": vstate.state[ch], **vstate.snapshot()}


def _apply_outputs():
    """Derive effective GPIO outputs from intent + software modes (lab spec):

      * ignition OFF -> everything off (master gate).
      * headlight / brake -> standalone, driven by their own intent.
      * horn -> not steady here; a click fires a beep pattern (see drive()).
      * reverse -> driven by intent (the 5 s timer manages it).
      * all_lamp mode -> forces head lamp + tail lamp (brake) ON, and turns
        BOTH indicators on.
      * hazard mode  -> both indicators on.
      * otherwise    -> left/right indicators follow their own toggle.

    Indicators are driven STEADY — the lamps have a hardware flasher, so we
    do NOT blink them in software.
    """
    on = vstate.state

    # Ignition is both a real output AND the master gate.
    hardware.set("ignition", on["ignition"])
    if not on["ignition"]:
        for c in _PIN_CHANNELS:
            hardware.set(c, False)
        return

    all_lamp = on["all_lamp"]

    # Standalone lamps. All-lamp mode forces head + tail (brake) lamps on.
    hardware.set("headlight", on["headlight"] or all_lamp)
    hardware.set("brake",     on["brake"]     or all_lamp)
    # Reverse/forward: driven by its intent, which the 5 s timer manages.
    hardware.set("reverse",   on["reverse"])

    # Indicators: ON under hazard OR all-lamp, else follow their own toggle.
    # Steady output — the hardware flasher blinks the actual lamps.
    if on["hazard"] or all_lamp:
        hardware.set("left_ind", True)
        hardware.set("right_ind", True)
    else:
        hardware.set("left_ind",  on["left_ind"])
        hardware.set("right_ind", on["right_ind"])


# --- Lifespan: warm up RAG + voice on startup --------------------------

class AppState:
    rag: Optional[RAGEngine] = None
    stt_backends: dict = {}
    tts_backends: dict = {}
    stt_mode: str = config.DEFAULT_STT       # "off" | "vosk" | "google"
    tts_mode: str = config.DEFAULT_TTS       # "off" | "browser" | "pyttsx3" | "gtts"
    tts_rate: int = config.PYTTSX3_RATE      # offline (pyttsx3) speech rate, words/min
    ready: bool = False
    error: Optional[str] = None


astate = AppState()


# Backend ids that are valid for each role. "browser" / "off" are
# handled client-side only; the server just records the preference.
_VALID_STT = ("off", "vosk", "google")
_VALID_TTS = ("off", "browser", "pyttsx3", "gtts")


def _warmup():
    try:
        astate.rag = RAGEngine()
    except Exception as e:
        astate.error = f"rag load failed: {e}"
        log.error(astate.error)
        return
    astate.stt_backends = voice.build_stt_backends()
    astate.tts_backends = voice.build_tts_backends()
    astate.ready = True
    log.info("warmup complete")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    threading.Thread(target=_warmup, daemon=True, name="warmup").start()
    yield
    hardware.shutdown()


# --- FastAPI app -------------------------------------------------------

app = FastAPI(title="EV Lab Dashboard", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).parent / "frontend"


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# --- State endpoints ---------------------------------------------------

@app.get("/api/state")
async def get_state():
    return {"ready": astate.ready, "error": astate.error, **vstate.snapshot()}


def _cpu_temp_c():
    """Raspberry Pi CPU temperature in °C (None off-Pi, e.g. Windows dev)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


@app.get("/api/temp")
async def get_temp():
    return {"temp_c": _cpu_temp_c()}


class ControlBody(BaseModel):
    action: str           # "toggle" | "press" | "release"


@app.post("/api/control/{channel}")
async def post_control(channel: str, body: ControlBody):
    return drive(channel, body.action)


# --- RAG ---------------------------------------------------------------

class AskBody(BaseModel):
    question: str


@app.post("/api/ask")
async def post_ask(body: AskBody):
    if not astate.ready or astate.rag is None:
        raise HTTPException(503, "engine still warming up")
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "empty question")
    out = await asyncio.to_thread(astate.rag.answer, q)
    return out


# --- Knowledge base (question bank: browse + live add + downloadable handout) -

_html_lock = threading.Lock()


def _regen_bank_html():
    """Background-regenerate the student HTML handout from the bank JSON."""
    def _run():
        try:
            with _html_lock:
                n = bank_html.render_to_file()
            log.info(f"bank HTML regenerated ({n} questions)")
        except Exception as e:
            log.error(f"bank HTML regen failed: {e}")
    threading.Thread(target=_run, daemon=True, name="bank-html").start()


class BankAddBody(BaseModel):
    question: str
    answer: str
    reference: str = ""


@app.get("/api/bank/list")
async def get_bank_list():
    if not astate.ready or astate.rag is None:
        raise HTTPException(503, "engine still warming up")
    items = await asyncio.to_thread(astate.rag.list_qa)
    return {"count": len(items), "items": items}


@app.post("/api/bank/add")
async def post_bank_add(body: BankAddBody):
    if not astate.ready or astate.rag is None:
        raise HTTPException(503, "engine still warming up")
    try:
        entry = await asyncio.to_thread(astate.rag.add_qa,
                                        body.question, body.answer, body.reference)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _regen_bank_html()                      # refresh the downloadable handout in the background
    return {"ok": True, "entry": entry, "count": len(astate.rag.bank)}


@app.get("/api/bank/download")
async def get_bank_download():
    if not config.BANK_HTML.exists():       # first download before any add
        await asyncio.to_thread(bank_html.render_to_file)
    return FileResponse(config.BANK_HTML, media_type="text/html",
                        filename="question_bank.html")


@app.delete("/api/bank/{qa_id}")
async def delete_bank(qa_id: str):
    # Only user-added questions (id 'user-...') are deletable; the base bank is
    # protected in rag_core.delete_qa() regardless of what the client sends.
    if not astate.ready or astate.rag is None:
        raise HTTPException(503, "engine still warming up")
    try:
        removed = await asyncio.to_thread(astate.rag.delete_qa, qa_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _regen_bank_html()
    return {"ok": True, "removed_id": removed.get("id"), "count": len(astate.rag.bank)}


# --- Switching / IoT mode: free-form command -> control --------------------
# The LLM classifies the utterance to one channel + on/off; the server enforces
# the ignition gate + channel type (toggle / momentary brake / trigger) and
# returns a templated confirmation message. Visual confirmation is the button
# state itself (updated live via /ws/state); the message is for speech + errors.

_SWITCH_LABELS = {
    "ignition": "Vehicle", "headlight": "Headlights", "all_lamp": "All-lamp mode",
    "hazard": "Hazard lights", "left_ind": "Left indicator", "right_ind": "Right indicator",
    "brake": "Brake", "horn": "Horn", "reverse": "Reverse",
}
_TOGGLE_CHANNELS = ("headlight", "all_lamp", "hazard", "left_ind", "right_ind")
_TRIGGER_CHANNELS = ("horn", "reverse")
_GATE_MSG = "The vehicle is off — turn it on first."


def _switch_result(ok, level, message):
    return {"ok": ok, "level": level, "message": message, **vstate.snapshot()}


class SwitchBody(BaseModel):
    text: str


@app.post("/api/switch")
async def post_switch(body: SwitchBody):
    if not astate.ready or astate.rag is None:
        raise HTTPException(503, "engine still warming up")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty command")

    intent = await asyncio.to_thread(astate.rag.classify_command, text,
                                     vstate.snapshot()["channels"])
    ch, act, err = intent.get("channel"), intent.get("action"), intent.get("error")

    if err == "not_a_control":
        return _switch_result(False, "error", "That isn't one of the vehicle controls.")
    if err or ch is None:
        return _switch_result(False, "error", "Sorry, I didn't catch a valid command.")

    label = _SWITCH_LABELS.get(ch, ch)
    desired = (act == "on")
    states = vstate.snapshot()["channels"]
    ign = states["ignition"]

    # Trigger channels: "on" fires it; "off" does nothing.
    if ch in _TRIGGER_CHANNELS:
        if not desired:
            return _switch_result(True, "ok", f"{label} is momentary — nothing to turn off.")
        if not ign:
            return _switch_result(False, "blocked", _GATE_MSG)
        drive(ch, "press")
        return _switch_result(True, "ok",
                              "Horn sounded." if ch == "horn" else "Reverse engaged for 5 seconds.")

    # Ignition: the master gate.
    if ch == "ignition":
        if ign == desired:
            return _switch_result(True, "ok", f"Vehicle already {'on' if desired else 'off'}.")
        drive("ignition", "toggle")
        return _switch_result(True, "ok", f"Vehicle {'on' if desired else 'off'}.")

    # Everything else needs ignition on.
    if not ign:
        if not desired:
            return _switch_result(True, "ok", f"{label} already off.")
        return _switch_result(False, "blocked", _GATE_MSG)

    if ch == "brake":
        drive("brake", "press" if desired else "release")
        return _switch_result(True, "ok", "Brake applied." if desired else "Brake released.")

    # Steady toggle channels: set to the desired state.
    if states.get(ch) == desired:
        return _switch_result(True, "ok", f"{label} already {'on' if desired else 'off'}.")
    res = drive(ch, "toggle")
    if res.get("ignored"):
        return _switch_result(False, "blocked", _GATE_MSG)
    return _switch_result(True, "ok", f"{label} {'on' if desired else 'off'}.")


# --- Transcribe --------------------------------------------------------
# Browser captures via Web Audio API, downsamples to 16 kHz int16 PCM,
# POSTs raw bytes. No ffmpeg, no format negotiation - the simplest path.

@app.post("/api/transcribe")
async def post_transcribe(request: Request):
    if not astate.ready:
        raise HTTPException(503, "engine still warming up")
    if astate.stt_mode == "off":
        raise HTTPException(409, "STT disabled in config")
    pcm = await request.body()
    if not pcm:
        return {"text": ""}
    backend = astate.stt_backends.get(astate.stt_mode)
    if not backend or not backend.available:
        err = (backend.error if backend else "backend not registered")
        raise HTTPException(500, f"STT backend '{astate.stt_mode}' unavailable: {err}")
    try:
        text = await asyncio.to_thread(backend.transcribe, pcm)
    except Exception as e:
        raise HTTPException(500, f"STT failed: {e}")
    return {"text": text}


# --- TTS (server-side audio bytes; browser SpeechSynthesis is also an option) -

@app.get("/api/tts")
async def get_tts(text: str = "", backend: str = ""):
    if not text:
        raise HTTPException(400, "empty text")
    mode = backend or astate.tts_mode
    if mode in ("off", "browser"):
        raise HTTPException(409, f"TTS mode '{mode}' is client-side, no server audio")
    be = astate.tts_backends.get(mode)
    if not be or not be.available:
        err = (be.error if be else "backend not registered")
        raise HTTPException(503, f"TTS backend '{mode}' unavailable: {err}")
    try:
        audio = await asyncio.to_thread(be.synth_bytes, text)
    except Exception as e:
        raise HTTPException(500, f"TTS failed: {e}")
    if not audio:
        raise HTTPException(500, "TTS produced no audio")
    return Response(content=audio, media_type=be.audio_mime)


# --- Voice config ------------------------------------------------------
# Lets the Config tab discover what's installed and switch backends live.

class VoiceConfigBody(BaseModel):
    stt: Optional[str] = None
    tts: Optional[str] = None
    tts_rate: Optional[int] = None


def _backend_info(backends: dict) -> dict:
    return {
        name: {"available": bool(b.available), "error": b.error}
        for name, b in backends.items()
    }


@app.get("/api/voice/config")
async def get_voice_config():
    return {
        "stt": astate.stt_mode,
        "tts": astate.tts_mode,
        "stt_options": ["off", "vosk", "google"],
        "tts_options": ["off", "browser", "pyttsx3", "gtts"],
        "stt_backends": _backend_info(astate.stt_backends),
        "tts_backends": _backend_info(astate.tts_backends),
        "tts_rate": astate.tts_rate,
        "ready": astate.ready,
    }


@app.post("/api/voice/config")
async def post_voice_config(body: VoiceConfigBody):
    if body.stt is not None:
        if body.stt not in _VALID_STT:
            raise HTTPException(400, f"invalid stt '{body.stt}'")
        astate.stt_mode = body.stt
    if body.tts is not None:
        if body.tts not in _VALID_TTS:
            raise HTTPException(400, f"invalid tts '{body.tts}'")
        astate.tts_mode = body.tts
    if body.tts_rate is not None:
        rate = max(80, min(300, int(body.tts_rate)))
        astate.tts_rate = rate
        be = astate.tts_backends.get("pyttsx3")
        if be is not None:
            be.rate = rate
    return {"stt": astate.stt_mode, "tts": astate.tts_mode, "tts_rate": astate.tts_rate}


# --- WebSocket ---------------------------------------------------------

@app.websocket("/ws/state")
async def ws_state(ws: WebSocket):
    await hub.add(ws)
    try:
        while True:
            await ws.receive_text()   # treat any inbound as keep-alive
    except WebSocketDisconnect:
        await hub.drop(ws)
    except Exception:
        await hub.drop(ws)


# --- Entry point -------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=config.SERVER_HOST, port=config.SERVER_PORT,
                reload=False, log_level="info")
