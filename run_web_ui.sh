#!/usr/bin/env bash
# ============================================================
#  EV Lab Dashboard - Web UI launcher (Raspberry Pi / Linux)
#  Starts the FastAPI server (foreground, so logs are visible).
#  Open http://127.0.0.1:8000 in Chromium once it's up.
#
#  Make executable once:  chmod +x run_web_ui.sh
#  Run:                   ./run_web_ui.sh
# ============================================================
set -u
cd "$(dirname "$0")"

echo
echo "=== EV Lab Dashboard ======================================"
echo " Folder : $(pwd)"
echo " URL    : http://127.0.0.1:8000"
echo "==========================================================="
echo

# --- 1. Sanity check: python3 present? ----------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 was not found on PATH. Install Python 3 and retry."
    exit 1
fi

# --- 2. Sanity check: Ollama reachable? (non-fatal) ---------
#     The dashboard still loads; only chat fails until Ollama is
#     up and qwen2.5:1.5b is pulled.
if command -v curl >/dev/null 2>&1; then
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:11434/api/tags || true)"
    if [ "$code" != "200" ]; then
        echo "[WARN]  Ollama not reachable at 127.0.0.1:11434"
        echo "        Start it and run:  ollama pull qwen2.5:1.5b"
        echo "        The dashboard will still load; only chat will fail."
        echo
    fi
fi

# --- 3. Launch the server (foreground) ----------------------
# Pick the Python: an already-activated venv first, then env/ .venv/ venv/,
# else system python3. First answer takes ~2 min on a Pi 5 (embedder + Ollama).
if   [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then PYBIN="$VIRTUAL_ENV/bin/python"
elif [ -x "env/bin/python" ];   then PYBIN="env/bin/python"
elif [ -x ".venv/bin/python" ]; then PYBIN=".venv/bin/python"
elif [ -x "venv/bin/python" ];  then PYBIN="venv/bin/python"
else PYBIN="python3"; echo "[info] no virtualenv found — using system python3."
fi
echo "[info] launching with: $PYBIN"
exec "$PYBIN" server.py
