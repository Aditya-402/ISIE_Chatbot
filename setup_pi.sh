#!/usr/bin/env bash
# ============================================================
#  EV Lab Dashboard — FIRST-TIME SETUP (Raspberry Pi / Linux)
# ============================================================
#  Run once on a fresh Pi to get the software ready:
#    1. installs the apt system packages it needs (needs sudo)
#    2. creates a local Python virtual environment (.venv)
#    3. downloads the Python libraries from PyPI
#    4. installs the Pi-only GPIO library (rpi-lgpio)
#    5. installs Ollama (if missing) and pulls the LLM model
#    6. verifies the bundled embedder + offline-STT models are present
#
#  The embedding model (bge-small) and the Vosk STT model SHIP inside
#  models/ — they are not downloaded, only verified.
#
#  Usage:
#    chmod +x setup_pi.sh
#    ./setup_pi.sh                 # full setup
#    SKIP_APT=1 ./setup_pi.sh      # skip the sudo apt step
#
#  After it finishes, start the dashboard with:  ./run_web_ui.sh
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
MODEL="qwen2.5:1.5b"

log()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[WARN] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Python present? -------------------------------------------------
command -v "$PY" >/dev/null 2>&1 || die "python3 not found. Install Python 3 and retry."
log "Using $("$PY" --version 2>&1)"

# --- 2. System packages (apt; needs sudo, skippable) --------------------
if command -v apt-get >/dev/null 2>&1; then
  if [ "${SKIP_APT:-0}" = "1" ]; then
    warn "SKIP_APT=1 — skipping system packages."
  else
    log "Installing system packages (sudo)…"
    sudo apt-get update
    sudo apt-get install -y \
        python3-venv python3-pip \
        portaudio19-dev libportaudio2 \
        espeak-ng \
        ffmpeg libsdl2-mixer-2.0-0 \
        swig python3-dev build-essential liblgpio-dev \
      || warn "apt step failed — continuing (install espeak-ng etc. manually)."
  fi
else
  warn "apt-get not found — skipping system packages."
fi

# --- 3. Python virtual environment --------------------------------------
log "Creating virtual environment: $VENV"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip

# --- 4. Python libraries (from PyPI / piwheels) -------------------------
# CPU-only PyTorch FIRST: the Pi has no NVIDIA GPU, but the default aarch64
# torch wheel drags in ~5 GB of unused CUDA libraries that can fill the disk.
log "Installing CPU-only PyTorch…"
pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
  || pip install --no-cache-dir "torch<2.7" \
  || warn "CPU torch install failed; requirements.txt may pull the large CUDA build."
log "Installing Python libraries (requirements.txt)…"
pip install --no-cache-dir -r requirements.txt

# --- 4b. Pi-only GPIO library -------------------------------------------
if [ "$(uname -s)" = "Linux" ]; then
  log "Installing Pi GPIO library (requirements-pi.txt)…"
  pip uninstall -y RPi.GPIO >/dev/null 2>&1 || true   # avoid clash with rpi-lgpio
  pip install --no-cache-dir -r requirements-pi.txt \
    || warn "rpi-lgpio build failed — need swig/python3-dev/build-essential (apt step above)."
else
  warn "Not Linux — skipping rpi-lgpio (GPIO runs in SIM mode here)."
fi

# --- 4c. Download models if missing (git-ignored, fetched on first setup)
log "Ensuring embedder + STT models are present (downloads once)…"
python - <<'PY'
import shutil, zipfile, urllib.request
from pathlib import Path

models = Path.cwd() / "models"
models.mkdir(exist_ok=True)

# 1) BGE embedder (retrieval) — from HuggingFace, lean file set only.
bge = models / "bge-small-en-v1.5"
if (bge / "model.safetensors").exists():
    print("  ok  embedder present")
else:
    print("  .. downloading BAAI/bge-small-en-v1.5 …")
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id="BAAI/bge-small-en-v1.5",
        local_dir=str(bge),
        allow_patterns=["*.json", "*.txt", "model.safetensors", "1_Pooling/*"],
    )
    print("  ok  embedder ready")

# 2) Vosk small English STT — zip from alphacephei, unpack to models/vosk-en.
vosk = models / "vosk-en"
if (vosk / "am" / "final.mdl").exists():
    print("  ok  Vosk STT present")
else:
    print("  .. downloading vosk-model-small-en-us-0.15 …")
    url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
    zpath = models / "_vosk.zip"
    urllib.request.urlretrieve(url, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(models)
    zpath.unlink()
    extracted = models / "vosk-model-small-en-us-0.15"
    if extracted.exists():
        if vosk.exists():
            shutil.rmtree(vosk)
        extracted.rename(vosk)
    print("  ok  Vosk STT ready")
PY

# --- 5. Ollama + LLM model ----------------------------------------------
if ! command -v ollama >/dev/null 2>&1; then
  log "Installing Ollama…"
  curl -fsSL https://ollama.com/install.sh | sh \
    || warn "Ollama install failed — install manually from https://ollama.com"
fi
if command -v ollama >/dev/null 2>&1; then
  # make sure the daemon is up before pulling
  if ! ollama list >/dev/null 2>&1; then
    log "Starting Ollama daemon…"
    nohup ollama serve >/tmp/ollama_serve.log 2>&1 &
    sleep 3
  fi
  log "Pulling LLM model: $MODEL (~1 GB, one time)…"
  ollama pull "$MODEL" || warn "Could not pull $MODEL — run 'ollama pull $MODEL' later."
else
  warn "Ollama unavailable — chat won't work until it's installed and '$MODEL' is pulled."
fi

# --- 6. Verify bundled models (shipped, not downloaded) -----------------
log "Checking bundled models…"
if [ -f models/bge-small-en-v1.5/model.safetensors ]; then
  echo "  ok  embedder        models/bge-small-en-v1.5"
else
  warn "embedder MISSING at models/bge-small-en-v1.5 — retrieval will fail."
fi
if [ -f models/vosk-en/am/final.mdl ]; then
  echo "  ok  offline STT     models/vosk-en"
else
  warn "Vosk STT MISSING at models/vosk-en — offline mic input will fail."
fi

log "Setup complete."
echo "Start the dashboard with:  ./run_web_ui.sh"
echo "Then open:                 http://127.0.0.1:8000"
