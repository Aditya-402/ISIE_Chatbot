#!/usr/bin/env bash
# ============================================================
#  EV Lab Dashboard - ONE-CLICK kiosk launcher (Raspberry Pi)
# ============================================================
#  The client double-clicks the "EV Lab Dashboard" desktop icon,
#  which runs this script. It:
#    1. Starts the FastAPI backend (only if it isn't already up),
#    2. Opens the UI fullscreen in Chromium (kiosk mode).
#  Exit the kiosk to the desktop any time with:  Ctrl + Alt + X
#
#  This finds its own folder, so it works wherever the repo lives.
# ============================================================
set -u
cd "$(dirname "$0")"
URL="http://127.0.0.1:8000"

# --- 1. Start the backend only if it isn't already answering ---------
#     (If kiosk_setup.sh installed the systemd service, the server is
#      already running and this step is skipped.)
if ! curl -s -o /dev/null "$URL/api/state"; then
  if   [ -x env/bin/python ];   then PYBIN="env/bin/python"
  elif [ -x .venv/bin/python ]; then PYBIN=".venv/bin/python"
  elif [ -x venv/bin/python ];  then PYBIN="venv/bin/python"
  else PYBIN="python3"; fi
  echo "[launch] starting backend with: $PYBIN"
  nohup "$PYBIN" server.py >/tmp/ev_dashboard.log 2>&1 &
fi

# --- 2. Open the fullscreen kiosk ------------------------------------
#     kiosk_chromium.sh waits for the server to warm up, relaunches
#     Chromium if it crashes, and stops cleanly on Ctrl+Alt+X.
exec ./kiosk_chromium.sh
