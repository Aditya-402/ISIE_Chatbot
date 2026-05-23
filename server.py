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
        """Schedule a broadcast from a worker thread (hardware blink loops)."""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)


hub = Hub()


# --- Control orchestration ---------------------------------------------
# Maps incoming actions to hardware.py + state mutations + WS broadcasts.

_blink_channels = {"left_ind", "right_ind"}


def drive(ch: str, action: str) -> dict:
    """Apply a control action and broadcast the resulting state.
    Returns the channel's new value.
    """
    if ch not in config.CONTROL_CHANNELS:
        raise HTTPException(404, f"unknown channel '{ch}'")

    if ch == "ignition" and action in ("toggle", "press"):
        new = not vstate.state["ignition"]
        vstate.apply("ignition", new)
        hardware.set("ignition", new)
        if not new:
            for other in config.CONTROL_CHANNELS:
                if other == "ignition":
                    continue
                if vstate.state[other]:
                    vstate.apply(other, False)
                    hardware.set(other, False)
                hardware.stop_blink(other)
        hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})
        return {"channel": ch, "on": new, **vstate.snapshot()}

    if not vstate.state["ignition"]:
        # Ignition gate: ignore other channels until ignition is on.
        return {"channel": ch, "on": False, "ignored": True, **vstate.snapshot()}

    if action == "toggle":
        new = not vstate.state[ch]
        vstate.apply(ch, new)
        _apply_toggle(ch, new)
    elif action == "set":
        # Reserved for direct set; expects ?value=true/false in querystring.
        raise HTTPException(400, "use POST body with action=set and value")
    elif action == "press":
        vstate.apply(ch, True)
        hardware.pulse_on(ch)
    elif action == "release":
        vstate.apply(ch, False)
        hardware.pulse_off(ch)
    else:
        raise HTTPException(400, f"unknown action '{action}'")

    hub.push_from_thread({"type": "snapshot", **vstate.snapshot()})
    return {"channel": ch, "on": vstate.state[ch], **vstate.snapshot()}


def _apply_toggle(ch: str, new: bool):
    """Toggle logic, including indicator interlocks and hazard."""
    # L/R mutual exclusion (turning one on cancels the other).
    if ch in _blink_channels and new:
        other = "right_ind" if ch == "left_ind" else "left_ind"
        if vstate.state[other]:
            vstate.apply(other, False)
            hardware.stop_blink(other)

    if ch == "hazard":
        if new:
            hardware.blink("left_ind")
            hardware.blink("right_ind")
        else:
            hardware.stop_blink("left_ind")
            hardware.stop_blink("right_ind")
            # Restore any individual indicator that was on.
            if vstate.state["left_ind"]:
                hardware.blink("left_ind")
            if vstate.state["right_ind"]:
                hardware.blink("right_ind")
    elif ch in _blink_channels:
        if vstate.state["hazard"]:
            pass   # hazard already drives both; intent recorded only
        elif new:
            hardware.blink(ch)
        else:
            hardware.stop_blink(ch)
    else:
        hardware.set(ch, new)


# --- Lifespan: warm up RAG + voice on startup --------------------------

class AppState:
    rag: Optional[RAGEngine] = None
    stt_backends: dict = {}
    tts_backends: dict = {}
    stt_mode: str = config.DEFAULT_STT       # "off" | "vosk" | "google"
    tts_mode: str = config.DEFAULT_TTS       # "off" | "browser" | "pyttsx3" | "gtts"
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
    return {"stt": astate.stt_mode, "tts": astate.tts_mode}


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
