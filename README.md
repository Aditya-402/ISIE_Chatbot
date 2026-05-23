# EV Lab Dashboard — Raspberry Pi 5 deployable

A self-contained offline-first IoT vehicle dashboard + EV chatbot for an EV
(electric vehicle) lab. FastAPI backend + vanilla HTML/CSS/JS frontend,
served as a full-screen kiosk on the official 7" touch panel.

Built on top of the research stack validated in `../Bench1` (qwen2.5:1.5b +
BGE-small + hybrid retrieval + retrieval-confidence gate).

The repo ships the code and the small corpus-specific vector indices. The
large models — the embedder and the offline speech model — are **not** in git
(too large for GitHub); `setup_pi.sh` downloads them on first run, alongside
Ollama + the LLM weights and the Pi-only GPIO dependency.

---

## Folder layout

```
EV_Chatbot_Pi5/
├── server.py             # FastAPI app — main entry point
├── frontend/
│   ├── index.html        # single-page shell: Dashboard / Bot / Config tabs
│   ├── styles.css        # dark automotive theme (font-size scales via --fs)
│   └── app.js            # tabs, controls, WS, mic, chat, voice + font config
├── rag_core.py           # hybrid retrieval + gate + Ollama call
├── voice.py              # STT / TTS backends (vosk, google, pyttsx3, gtts)
├── hardware.py           # GPIO abstraction (SIM on Windows, real on Pi)
├── config.py             # single source of truth for settings
├── requirements.txt      # cross-platform deps (Windows dev + Pi)
├── requirements-pi.txt   # Pi-ONLY GPIO dep (rpi-lgpio) — see below
├── setup_pi.sh           # first-time setup: venv + deps + Ollama model
├── run_web_ui.bat        # Windows launcher
├── run_web_ui.sh         # Raspberry Pi / Linux launcher (uses .venv)
├── README.md             (this file)
├── rag_data/             # ~9 MB — vector + sparse indices + chunk texts
│   ├── faiss.bge.index
│   ├── chunk_map.json
│   └── bm25.pkl
├── models/               # git-ignored; downloaded by setup_pi.sh on first run
│   ├── bge-small-en-v1.5/   # ~135 MB — embedder for retrieval (from HuggingFace)
│   └── vosk-en/             # ~40 MB  — offline English STT (from alphacephei)
└── legacy_tk/            # the original Tkinter app (kept as fallback)
```

Repo size: **~9 MB** (code + `rag_data/` indices). `setup_pi.sh` then pulls
~175 MB of models and ~1 GB of qwen2.5:1.5b LLM weights on first run.

---

## Quick start (Windows, dev)

```powershell
# 1. install Python deps (one time) — do NOT install requirements-pi.txt here
pip install -r requirements.txt

# 2. install Ollama from https://ollama.com  →  then pull the model
ollama pull qwen2.5:1.5b

# 3. launch the server (or just double-click run_web_ui.bat)
python server.py
# → open http://127.0.0.1:8000 in any browser
```

The first answer takes ~30 s on Windows while the embedder loads and Ollama
warms up. The dashboard UI loads instantly; the chat status pill turns green
("ready") once the engine is warm.

---

## Deploy to Raspberry Pi 5 (Linux)

### Easy path (one-shot setup)

```bash
scp -r EV_Chatbot_Pi5/ pi@<pi-ip>:~/        # copy the folder
ssh pi@<pi-ip>
cd ~/EV_Chatbot_Pi5
chmod +x setup_pi.sh run_web_ui.sh
./setup_pi.sh          # apt deps + venv + pip libs + Ollama + model (one time)
./run_web_ui.sh        # start the server, then open http://127.0.0.1:8000
```

`setup_pi.sh` installs the apt packages (sudo), creates a `.venv`, downloads
the Python libraries, installs the Pi GPIO library, installs Ollama and pulls
`qwen2.5:1.5b`, and verifies the bundled embedder + Vosk models. Use
`SKIP_APT=1 ./setup_pi.sh` to skip the system-package step. The manual steps
below do the same thing by hand.

### 1. System packages (one time)

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv \
                    portaudio19-dev libportaudio2 \
                    espeak-ng \
                    ffmpeg libsdl2-mixer-2.0-0
```

What each is for:
- `espeak-ng` — voice for offline server-side TTS (`pyttsx3`); this is the
  **default TTS on the Pi** (browser SpeechSynthesis usually has no offline
  voices on Chromium/Linux).
- `portaudio19-dev` + `libportaudio2` — server-side mic capture for the
  Tkinter fallback only; the web app captures mic in the browser.
- `ffmpeg` + `libsdl2-mixer-2.0-0` — for the optional gTTS playback path.

### 2. Copy the folder

```bash
scp -r EV_Chatbot_Pi5/ pi@<pi-ip>:~/
```

### 3. Install Python deps (main + Pi GPIO)

```bash
cd ~/EV_Chatbot_Pi5
pip3 install -r requirements.txt
pip3 install -r requirements-pi.txt      # Pi-only: rpi-lgpio (see GPIO below)
```

> **Pi 5 GPIO note:** the classic `RPi.GPIO` library does **not** work on the
> Pi 5's RP1 I/O controller. `requirements-pi.txt` installs **`rpi-lgpio`**, a
> drop-in that provides the same `import RPi.GPIO as GPIO` API on top of
> lgpio — so no code changes are needed. Do **not** also install the classic
> `RPi.GPIO`; the two conflict (`pip3 uninstall -y RPi.GPIO` first if present).

### 4. Install Ollama + the LLM (one-time, needs internet)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b      # ~1 GB
```

### 5. Run

```bash
chmod +x run_web_ui.sh        # one time
./run_web_ui.sh               # foreground, logs visible
```

Then open `http://127.0.0.1:8000` in Chromium. For a borderless kiosk:

```bash
chromium-browser --kiosk --noerrdialogs --disable-translate \
                 --app=http://127.0.0.1:8000
```

The first answer takes ~2 min on a Pi 5 while the embedder + Ollama warm up;
subsequent answers ~2-3 min on the 1.5b model. Gate refusals are instant.

> Auto-boot (systemd service + kiosk autostart) is intentionally **not**
> set up in this build — launch is manual. Ask if you want it added.

---

## GPIO mapping (`hardware.py`)

Logical channel names are mapped to BCM pins in two dicts at the top of
`hardware.py`. The UI/server never reference pin numbers, so wiring changes
touch only this file.

```python
PIN_MAP = {            # logical channel -> BCM GPIO pin
    "ignition":      27,   # pin 13  (hardware signal AND master gate)
    "headlight":     17,   # pin 11
    "horn":          22,   # pin 15
    "left_ind":       5,   # pin 29
    "right_ind":      6,   # pin 31
    "brake":         13,   # pin 33  (tail lamp)
    # hazard + all_lamp are SOFTWARE signals (no pin); reverse + parking_brake
    # are UI-only until you add them here.
}
ACTIVE_HIGH = {}       # list a channel here ONLY if its relay switches ON on HIGH
```

| Signal | BCM | Pin | Type |
|--------|-----|-----|------|
| ignition | 27 | 13 | hardware **and** master gate |
| headlight | 17 | 11 | standalone |
| horn | 22 | 15 | standalone — click fires a beep pattern |
| left_ind | 5 | 29 | standalone (blinks in hazard/all-lamp) |
| right_ind | 6 | 31 | standalone (blinks in hazard/all-lamp) |
| brake | 13 | 33 | standalone / tail lamp |
| hazard | — | — | **software** mode (blinks both indicators) |
| all_lamp | — | — | **software** mode (head+tail on, indicators blink) |
| reverse, parking_brake | — | — | UI-only, unwired |

Rules:
- Values are **BCM** numbers (`GPIO.setmode(GPIO.BCM)`). Avoid 2/3 (I²C) and
  14/15 (UART) if you use those buses.
- **ignition** drives a pin *and* gates everything: while it's off, all other
  outputs are forced off.
- **hazard** and **all_lamp** are software modes — they have no pins of their
  own; they drive the indicator/lamp pins above.
- **Active-low is the default** — most relay boards switch ON when the pin
  goes LOW, so leave them out of `ACTIVE_HIGH`. Add `"name": True` only for
  active-high channels. On boot every mapped pin is initialised to its OFF
  level per polarity, so active-high relays don't fire at startup.
- Wire each load's relay input to its BCM pin and a common **GND** (e.g.
  physical pin 9, 14, 25, 34, or 39) to the relay board.
- Indicator blink is software-driven on a **3 s cycle**
  (`config.INDICATOR_BLINK_PERIOD_S`) — no hardware flasher needed.

`hardware.py` runs in SIM mode on Windows (state changes just log to stdout)
and REAL mode on Linux (drives GPIO via rpi-lgpio).

---

## UI — three tabs

A top tab-bar switches between three full-screen views; the mic/send input
bar at the bottom is shared.

- **DASHBOARD** — the vehicle gauge cluster + a control grid of touch buttons.
- **BOT** — the chat view (answers + source chips + latency/gate meta).
- **CONFIG** — voice backend selection and display (font) options.

### Dashboard controls

Ten logical channels drive GPIO via `hardware.py`:

| Channel       | Behaviour                                                  |
|---------------|------------------------------------------------------------|
| ignition      | Latched master gate (also a GPIO output). Greys out every other button when OFF. Title bar flips green when ON. |
| headlight     | Toggle                                                     |
| all_lamp      | Software mode. Forces head lamp + tail lamp (brake) ON and blinks both indicators together on a 3 s cycle. |
| hazard        | Software mode. Blinks both indicators together on a 3 s cycle. |
| left_ind      | Toggle (steady). Mutually exclusive with right_ind. Blinks only under hazard / all-lamp. |
| right_ind     | Toggle (steady). Same.                                     |
| horn          | Click → fixed beep pattern (2 s on, 3 s gap, 2 s on, off). Tunable in config.py. |
| brake         | Momentary — active while pressed (tail lamp).             |
| reverse       | Toggle (UI-only until wired).                              |
| parking_brake | Toggle (UI-only). Lights the red P tell-tale (shares the brake tell-tale). |

Turning ignition OFF cancels all active states and stops every blink/beep loop.

### Config tab

- **Speech-to-Text:** Off / Vosk (offline) / Google (online) — switched live;
  the MIC button greys out when STT is Off.
- **Text-to-Speech:** Off / Browser / pyttsx3 (offline server) / gTTS (online
  server) — switched live, with a "Test voice" button. READY/MISSING/CLIENT
  badges show what's installed.
- **Text size:** Small / Medium / Large / Extra-large.
- **Font style:** System / Inter-Sans / Verdana (legible) / Serif / Monospace.

Font choices apply to the whole interface (via the `--fs` size multiplier and
`--font` family CSS variables on `<html>`) and persist per-device in
`localStorage`. They're client-side only — no server or config.py change.

---

## Chat mode (BOT tab)

**Voice:** the browser captures audio via the Web Audio API, downsamples to
16 kHz int16 in JS, and POSTs raw PCM to `/api/transcribe`. The server hands
it to Vosk (offline) — no ffmpeg, no codec negotiation. The reply is read
aloud through the selected TTS backend.

**TTS routing** (set in the Config tab):
- `browser` — `window.speechSynthesis` (default on Windows; often silent on
  Chromium/Linux which lacks offline voices).
- `pyttsx3` — server-side espeak-ng audio (**default on the Pi**).
- `gtts` — server-side Google MP3 (needs internet).
- `off` — no spoken replies.

---

## How it answers (one-paragraph design summary)

For every question:

1. **Retrieve** — hybrid search (BM25 + BGE cosine, fused with Reciprocal
   Rank Fusion) returns the top 3 chunks from the 1612-chunk corpus.
2. **Gate** — a 2-signal retrieval-confidence gate checks the top chunk's
   scores. If `dense_top1 < 0.60` OR `(dense_top1 < 0.70 AND hybrid_top1 <
   0.020)`, the LLM is skipped entirely and the bot replies *"Out of scope for
   the EV lab knowledge base."* This stops the small LLM from leaking
   training-data priors on car prices, pop-culture, weather, etc.
3. **Answer** — if the gate passes, the top 3 chunks are sent as context to
   `qwen2.5:1.5b` via Ollama with a strict 40-word, beginner-friendly prompt.

The gate thresholds and the system prompt come from the calibrated research
run on `gold_questions_beginner_50.json` (see `../Bench1/research_log.html`).

---

## API surface (server.py)

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/`                       | serves `frontend/index.html` |
| GET    | `/static/*`               | serves frontend assets |
| GET    | `/api/state`              | full vehicle state + warmup status |
| POST   | `/api/control/{channel}`  | body `{"action":"toggle"|"press"|"release"}` |
| POST   | `/api/ask`                | body `{"question":"..."}` → answer JSON |
| POST   | `/api/transcribe`         | raw PCM16 (16 kHz mono LE) body → `{"text":...}` |
| GET    | `/api/tts`                | `?text=...&backend=pyttsx3|gtts` → audio bytes |
| GET    | `/api/voice/config`       | current STT/TTS + installed-backend availability |
| POST   | `/api/voice/config`       | body `{"stt":...,"tts":...}` → switch backend live |
| WS     | `/ws/state`               | pushes full state snapshot on every change |

---

## Tweaking

Everything tunable lives in **`config.py`**:

- `LLM_MODEL` — swap to a different Ollama model
- `GATE_ENABLED`, `GATE_DENSE`, `GATE_DENSE_STRICT`, `GATE_HYBRID` — gate calibration
- `MAX_ANSWER_WORDS`, `NUM_PREDICT`, `TEMPERATURE` — generation knobs
- `DEFAULT_STT`, `DEFAULT_TTS` — backend selection (TTS is Pi-aware by default)
- `SERVER_HOST`, `SERVER_PORT` — bind address (use `0.0.0.0` for LAN access)
- `INDICATOR_BLINK_PERIOD_S` — indicator blink cadence (3 s cycle) for hazard / all-lamp
- `HORN_BEEP_ON_S`, `HORN_BEEP_GAP_S`, `HORN_BEEP_COUNT` — horn beep pattern
- `CONTROL_CHANNELS` — logical channel registry (mirrored by `hardware.py`)

Font size/style are **not** in config.py — they're per-device browser
settings chosen in the Config tab.

No code change needed for these — edit `config.py`, restart `server.py`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Browser shows "server unreachable" | `server.py` not running, or wrong port. Check the launcher output. |
| Status pill stays on "warming up…" | RAG engine still loading. ~30 s on Windows, ~2 min on Pi 5. |
| Buttons disabled / greyed | Ignition is OFF — press IGNITION first. Every other channel is gated on it. |
| `*.sh: bad interpreter` | CRLF line endings (copied via Windows). Fix: `sed -i 's/\r$//' setup_pi.sh run_web_ui.sh`. |
| GPIO does nothing on the Pi | `rpi-lgpio` not installed (`pip3 install -r requirements-pi.txt`), or the channel isn't in `PIN_MAP`. Check the server log for "GPIO ready (BCM pins)". |
| Relay fires on boot / inverted | Wrong polarity. Add the channel to `ACTIVE_HIGH` (or remove it) in `hardware.py`. |
| MIC button greyed | STT is set to Off in the Config tab, or the browser denied mic permission. |
| No spoken replies on the Pi | Set TTS to **pyttsx3** in the Config tab and ensure `espeak-ng` is installed. Browser TTS is often silent on Chromium/Linux. |
| Indicator arrow doesn't blink | Confirm the WebSocket is connected (DevTools → Network → WS). |
| Chat answer never arrives | Ollama not running, or model not pulled. Run `ollama list` to confirm `qwen2.5:1.5b`. |
| Slow on Pi 5 | Normal. First answer ~2 min, subsequent ~2-3 min on a 1.5b model. Gate refusals are instant. |

---

## Legacy Tkinter UI

The original Tkinter dashboard lives in `legacy_tk/` and is kept as a
fallback. The web stack is the recommended deploy target.

```bash
python3 legacy_tk/app.py        # multi-mode dashboard
python3 legacy_tk/chatbot.py    # chat-only fallback
```
