"""Single source of truth for all settings.

All paths are RELATIVE to this file's directory, so the whole folder is
copy-paste portable between Windows (dev) and Raspberry Pi 5 (deploy).
"""

import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Auto-detect: Windows = sim (no GPIO), Linux = real (Pi 5 target).
SIM_MODE = platform.system() != "Linux"

# --- Bundled assets (everything below MUST ship with the folder) ----------

RAG_DATA_DIR   = ROOT / "rag_data"
FAISS_INDEX    = RAG_DATA_DIR / "faiss.bge.index"
CHUNK_MAP      = RAG_DATA_DIR / "chunk_map.json"
BM25_PKL       = RAG_DATA_DIR / "bm25.pkl"

EMBED_MODEL_DIR = ROOT / "models" / "bge-small-en-v1.5"   # ~135 MB
VOSK_MODEL_DIR  = ROOT / "models" / "vosk-en"             # ~40 MB

# --- External services (NOT bundled) --------------------------------------
# Ollama must be installed separately and the model pulled on first launch:
#   ollama pull qwen2.5:1.5b

OLLAMA_HOST = "127.0.0.1:11434"
LLM_MODEL   = "qwen2.5:1.5b"

# --- Retrieval ------------------------------------------------------------

TOP_K              = 3       # candidates retrieved (the gate inspects the top one)
HYBRID_OVER_K      = 15      # candidates each retriever returns before RRF
RRF_K              = 60      # Cormack et al. constant

# Context actually fed to the LLM. Latency lever (validated 2026-05-24 on Pi 5):
# the dominant per-question cost is prompt-eval of the retrieved text, so feeding
# fewer/shorter chunks slashes time-to-first-token without hurting accuracy.
CONTEXT_TOP_K      = 1       # how many top chunks go into the prompt
CONTEXT_WORD_CAP   = 100     # truncate each fed chunk to this many words

# --- Retrieval-confidence gate (Discovery 12, calibrated 2026-05-22) ------
# A 2-signal gate that short-circuits the LLM with "out of scope" when
# the top retrieved chunk's scores indicate the question is not answerable
# from the corpus. See the bundled README for the calibration data.

GATE_ENABLED          = True
GATE_DENSE            = 0.60   # hard rule
GATE_DENSE_STRICT     = 0.70   # joint rule: dense<this AND hybrid<below
GATE_HYBRID           = 0.020

REFUSAL_TEXT = "Out of scope for the EV lab knowledge base."

# --- Q&A-bank cache (Tier 1, ahead of retrieval) --------------------------
# Students are given the question bank and type questions from it, so most
# queries are (reworded) bank questions. A cache of the bank's Q&A pairs answers
# those instantly with the vetted gold answer - no LLM call. Validated 2026-05-24
# on Pi 5: T_CACHE=0.92 gave 100% precision on paraphrased bank questions, while
# off-script questions stay below threshold and fall through to RAG. The bank
# embedded in faiss.cache.index MUST match the bank handed to students - rebuild
# with build_cache.py whenever the bank changes.

CACHE_ENABLED = True
CACHE_INDEX   = RAG_DATA_DIR / "faiss.cache.index"
CACHE_MAP     = RAG_DATA_DIR / "cache_map.json"
T_CACHE       = 0.92

# Editable source bank (added to live from the Knowledge Base tab) + the
# student HTML handout regenerated whenever a question is added.
BANK_JSON = ROOT / "question_bank" / "qa_bank_all.json"
BANK_HTML = ROOT / "question_bank" / "question_bank.html"

# --- LLM generation -------------------------------------------------------

MAX_ANSWER_WORDS = 40
NUM_PREDICT      = 60          # ollama token budget (~45 words; answer capped at 40)
TEMPERATURE      = 0

# Lean prompt (validated 2026-05-24): a shorter system prompt + small context keep
# time-to-first-token low. Keeps the strict scope rule, refusal line, word cap, and
# "quote exact numbers" guidance. The fuller prompt is in git history if more
# stylistic guidance is wanted (re-test latency if you lengthen it).
RAG_SYSTEM_PROMPT = (
    "You are an EV tutor for beginners. Answer using ONLY the context; do not use "
    "outside knowledge. If the context does not answer the question (or it is unrelated "
    "to EV technology - e.g. consumer car prices or buying advice, weather, sports, "
    f"recipes), reply with EXACTLY this line and nothing else: {REFUSAL_TEXT} "
    f"Otherwise answer in at most {MAX_ANSWER_WORDS} words, in plain beginner-friendly "
    "language. Lead with the direct answer; quote exact numbers and technical terms "
    "from the context."
)

# --- Voice (Tkinter UI radio buttons select between these) ----------------

DEFAULT_STT = "vosk"          # one of: "off", "vosk", "google"
# Browser speechSynthesis usually has NO offline voices on Chromium/Linux,
# so on the Pi default to server-side pyttsx3 (espeak-ng); browser on Windows.
# Either way it's switchable live from the Config tab.
DEFAULT_TTS = "browser" if SIM_MODE else "pyttsx3"   # "off"|"browser"|"pyttsx3"|"gtts"

# Offline (pyttsx3 / espeak) speech rate in words-per-minute. espeak's own
# default (~200) sounds fast; lower = slower/clearer. Adjustable on the Config tab.
PYTTSX3_RATE = 160

MIC_SAMPLE_RATE = 16000        # Vosk requires 16 kHz mono
MIC_CHANNELS    = 1

# --- UI -------------------------------------------------------------------

UI_TITLE        = "EV Lab Chatbot"
UI_WINDOW_SIZE  = "880x680"      # chat-only fallback (chatbot.py)
UI_FONT_FAMILY  = "Segoe UI"     # falls back gracefully on Linux
UI_FONT_SIZE    = 11

# --- Dashboard / multi-mode shell (app.py) --------------------------------
# Target hardware: Raspberry Pi 5 with the official 7" touch panel (800x480).

APP_TITLE        = "IOT ELECTRIC VEHICLE DASHBOARD"
APP_TITLE_CHAT   = "VEHICLE VOICE CONTROL & CHAT BOT"
APP_WINDOW_SIZE  = "800x480"
APP_FULLSCREEN   = True          # ESC exits regardless

# Title-bar colours (purple OFF, green when ignition is ON).
COLOUR_TITLE_OFF = "#4c2a8a"
COLOUR_TITLE_ON  = "#198754"
COLOUR_TITLE_FG  = "#ffffff"

# Dashboard look.
COLOUR_BG          = "#0e1b3a"   # deep blue background of vehicle-state panel
COLOUR_BTN_BG      = "#5aa9e6"   # light blue button
COLOUR_BTN_ACTIVE  = "#ffcc33"   # button when its state is ON
COLOUR_BTN_FG      = "#0e1b3a"
COLOUR_LANE        = "#7ec8ff"
COLOUR_ICON_OFF    = "#3a5c93"   # dim icon (state off)
COLOUR_ICON_ON     = "#ffffff"   # lit icon
COLOUR_HAZARD      = "#e63946"   # hazard / warning red
COLOUR_PANEL_FG    = "#ffffff"

# Indicator lamps have a HARDWARE flasher, so the software drives them steady
# (no blink cadence here).

# Horn: a single click fires a fixed beep pattern, then auto-off (not a latch).
# Default: beep twice (2 s each) with a 3 s gap between, then off.
HORN_BEEP_ON_S  = 2.0   # each beep duration (seconds)
HORN_BEEP_GAP_S = 3.0   # gap between beeps (seconds)
HORN_BEEP_COUNT = 2     # number of beeps

# Reverse/forward: keying it on drives the pin for this long, then auto-off.
REVERSE_PULSE_S = 5.0

# Power-on self-test: <delay> after ignition ON, drive the brake output for
# <hold> while flashing the PARKING-BRAKE tell-tale (not the brake tell-tale).
BRAKE_TEST_DELAY_S = 0.5
BRAKE_TEST_HOLD_S  = 1.0

# Mode registry order (left-to-right on the BOT toggle strip).
MODE_ORDER       = ("switching", "conversational")
DEFAULT_MODE     = "switching"

# --- Web server (server.py) -----------------------------------------------

SERVER_HOST   = "127.0.0.1"   # 0.0.0.0 to expose on LAN
SERVER_PORT   = 8000

# Logical control channel names. switching.py uses these; hardware.py maps
# them to GPIO pins once wiring is decided.
CONTROL_CHANNELS = (
    "headlight",
    "ignition",
    "horn",
    "all_lamp",
    "reverse",
    "hazard",
    "left_ind",
    "brake",
    "right_ind",
    "parking_brake",
)
