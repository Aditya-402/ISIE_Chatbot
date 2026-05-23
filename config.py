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

TOP_K              = 3       # chunks fed to the LLM
HYBRID_OVER_K      = 15      # candidates each retriever returns before RRF
RRF_K              = 60      # Cormack et al. constant

# --- Retrieval-confidence gate (Discovery 12, calibrated 2026-05-22) ------
# A 2-signal gate that short-circuits the LLM with "out of scope" when
# the top retrieved chunk's scores indicate the question is not answerable
# from the corpus. See the bundled README for the calibration data.

GATE_ENABLED          = True
GATE_DENSE            = 0.60   # hard rule
GATE_DENSE_STRICT     = 0.70   # joint rule: dense<this AND hybrid<below
GATE_HYBRID           = 0.020

REFUSAL_TEXT = "Out of scope for the EV lab knowledge base."

# --- LLM generation -------------------------------------------------------

MAX_ANSWER_WORDS = 40
NUM_PREDICT      = 120         # ollama token budget
TEMPERATURE      = 0

RAG_SYSTEM_PROMPT = (
    "You are an EV (electric vehicle) tutor for beginner lab students.\n"
    "STRICT SCOPE RULE: Answer using ONLY the facts in the provided context. "
    "You MUST NOT use any outside knowledge, training data, or general world knowledge. "
    "If the provided context does NOT directly answer the question (or the question is unrelated to EV technology - e.g. consumer car prices, weather, sports, recipes, personal advice), "
    "you MUST reply with EXACTLY this single line and nothing else: "
    f"{REFUSAL_TEXT}\n"
    f"Write at most {MAX_ANSWER_WORDS} words. Stop writing after {MAX_ANSWER_WORDS} words.\n"
    "Explain in simple, beginner-friendly language. Avoid unexplained jargon - "
    "if a technical term is needed, add a short plain-English meaning in parentheses "
    "(e.g. 'BMS (battery management system)').\n"
    "Lead with the direct answer; skip preamble like 'According to the context'.\n"
    "Quote exact numbers, names, and technical terms from the context - do NOT paraphrase them.\n"
    "If the question asks for items, options, or steps, give the complete list before any explanation.\n"
    "Do NOT include information that doesn't directly answer the question, even if it's in the context.\n"
    "Do NOT include a source line - it will be added automatically."
)

# --- Voice (Tkinter UI radio buttons select between these) ----------------

DEFAULT_STT = "vosk"          # one of: "off", "vosk", "google"
# Browser speechSynthesis usually has NO offline voices on Chromium/Linux,
# so on the Pi default to server-side pyttsx3 (espeak-ng); browser on Windows.
# Either way it's switchable live from the Config tab.
DEFAULT_TTS = "browser" if SIM_MODE else "pyttsx3"   # "off"|"browser"|"pyttsx3"|"gtts"

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
