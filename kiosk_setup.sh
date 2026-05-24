#!/usr/bin/env bash
# ============================================================
#  EV Lab Dashboard — KIOSK MODE setup (Raspberry Pi 5 / labwc)
# ============================================================
#  Run ONCE on the Pi, with sudo:    sudo ./kiosk_setup.sh
#
#  Configures unattended kiosk operation:
#    1. systemd service  -> the FastAPI server auto-starts on boot + restarts on crash
#    2. desktop autologin -> boots straight into the GUI, no login prompt
#    3. screen blanking OFF -> the panel stays on
#    4. labwc autostart  -> launches Chromium fullscreen at the dashboard
#    5. Ctrl+Alt+X keybind -> exit the kiosk to the desktop (SSH also works)
#
#  After it finishes:  sudo reboot   -> the Pi comes up in the kiosk.
#  To undo: `sudo systemctl disable --now ev-dashboard`, remove
#  ~/.config/labwc/autostart, and `sudo raspi-config` -> Boot -> Console.
# ============================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo ./kiosk_setup.sh" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
PYBIN="$REPO/env/bin/python"
[ -x "$PYBIN" ] || PYBIN="$REPO/.venv/bin/python"
[ -x "$PYBIN" ] || PYBIN="$(command -v python3)"

echo "Repo=$REPO  user=$RUN_USER  home=$USER_HOME  python=$PYBIN"

# --- 1. systemd service: the dashboard server --------------------------
echo "[1/5] systemd service ev-dashboard.service ..."
cat > /etc/systemd/system/ev-dashboard.service <<EOF
[Unit]
Description=EV Lab Dashboard (FastAPI server)
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$REPO
ExecStart=$PYBIN server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ev-dashboard.service
systemctl restart ev-dashboard.service

# --- 2. desktop autologin ----------------------------------------------
echo "[2/5] desktop autologin ..."
raspi-config nonint do_boot_behaviour B4 || echo "  (set Boot -> Desktop Autologin manually if this failed)"

# --- 3. screen blanking off --------------------------------------------
echo "[3/5] screen blanking off ..."
raspi-config nonint do_blanking 1 || true

# --- 4. labwc autostart: Chromium kiosk --------------------------------
echo "[4/5] labwc autostart ..."
LABWC="$USER_HOME/.config/labwc"
mkdir -p "$LABWC"
cat > "$LABWC/autostart" <<EOF
# Keep the standard Pi desktop running underneath, so Ctrl+Alt+X drops to a
# usable desktop; the kiosk browser runs fullscreen on top.
[ -f /etc/xdg/labwc/autostart ] && . /etc/xdg/labwc/autostart
# EV Lab dashboard kiosk
$REPO/kiosk_chromium.sh &
EOF
chmod +x "$REPO/kiosk_chromium.sh" "$REPO/kiosk_exit.sh"

# --- 5. Ctrl+Alt+X exit keybind (labwc rc.xml) -------------------------
echo "[5/5] Ctrl+Alt+X exit keybind ..."
RC="$LABWC/rc.xml"
KEYBIND="<keyboard><keybind key=\"C-A-x\"><action name=\"Execute\"><command>$REPO/kiosk_exit.sh</command></action></keybind></keyboard>"
if [ -f "$RC" ]; then
  if ! grep -q "kiosk_exit.sh" "$RC"; then
    cp "$RC" "$RC.bak.$(date +%s)"
    if grep -q "</keyboard>" "$RC"; then
      # merge into an existing <keyboard> block
      sed -i "s#</keyboard>#  <keybind key=\"C-A-x\"><action name=\"Execute\"><command>$REPO/kiosk_exit.sh</command></action></keybind>\n</keyboard>#" "$RC"
    else
      sed -i "s#</openbox_config>#$KEYBIND\n</openbox_config>#" "$RC"
    fi
  fi
else
  cat > "$RC" <<EOF
<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
$KEYBIND
</openbox_config>
EOF
fi

chown -R "$RUN_USER":"$RUN_USER" "$LABWC"

echo
echo "Kiosk configured. Reboot to start it:   sudo reboot"
echo "Exit kiosk to desktop any time:          Ctrl + Alt + X"
echo "(The server runs as a service now — don't also launch ./run_web_ui.sh.)"
