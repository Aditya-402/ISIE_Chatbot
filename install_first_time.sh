#!/usr/bin/env bash
# ============================================================
#  EV Lab Dashboard - FIRST-TIME INSTALLER (fresh Raspberry Pi)
# ============================================================
#  For NON-TECHNICAL users. Run this ONCE on a fresh Pi.
#  It does everything (takes ~20-40 min, needs internet):
#    1. Repairs file permissions + line endings (lost when zipped on Windows)
#    2. Installs all software + the AI model        (setup_pi.sh)
#    3. Puts a double-click "EV Lab Dashboard" icon on the Desktop
#    4. Turns on kiosk auto-start on power-on        (kiosk_setup.sh)
#    5. Reboots into the fullscreen app
#  Exit the kiosk later with:  Ctrl + Alt + X
# ============================================================
set -u
cd "$(dirname "$0")"
REPO="$(pwd)"

echo
echo "==================================================================="
echo "  EV Lab Dashboard - first-time install"
echo "  Folder : $REPO"
echo "  This takes 20-40 minutes and needs internet. Please keep it open."
echo "  If it asks for a password, type your Pi login password."
echo "==================================================================="
echo

# --- 1. Repair Windows-zip damage: CRLF -> LF and make scripts runnable
echo "[1/5] Repairing line endings and permissions ..."
for f in *.sh; do
  [ -f "$f" ] && sed -i 's/\r$//' "$f" && chmod +x "$f"
done
[ -f EV-Lab-Dashboard.desktop ] && sed -i 's/\r$//' EV-Lab-Dashboard.desktop

# --- 2. Install software + AI model (longest step) -------------------
echo "[2/5] Installing software and the AI model (this is the long part) ..."
./setup_pi.sh

# --- 3. Desktop launcher icon, pointing at THIS folder ---------------
echo "[3/5] Creating the 'EV Lab Dashboard' desktop icon ..."
DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/EV-Lab-Dashboard.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=EV Lab Dashboard
Comment=Start the EV Lab kiosk (fullscreen). Exit with Ctrl+Alt+X.
Exec=$REPO/launch_dashboard.sh
Path=$REPO
Icon=$REPO/frontend/logo.png
Terminal=false
Categories=Education;
StartupNotify=false
EOF
chmod +x "$DESKTOP_DIR/EV-Lab-Dashboard.desktop"
# best-effort: mark the icon "trusted" so the desktop runs it without warning
gio set "$DESKTOP_DIR/EV-Lab-Dashboard.desktop" metadata::trusted true 2>/dev/null || true

# --- 4. Kiosk auto-start on power-on (needs admin) -------------------
echo "[4/5] Turning on kiosk auto-start ..."
sudo ./kiosk_setup.sh

# --- 5. Reboot into the kiosk ----------------------------------------
echo
echo "[5/5] Done! The Pi will RESTART in 15 seconds and open the app."
echo "      After restart the app fills the screen automatically."
echo "      To exit the app to the desktop later:  Ctrl + Alt + X"
echo
for s in $(seq 15 -1 1); do
  printf "\r  Restarting in %2d s ...  (close this window to cancel)" "$s"
  sleep 1
done
echo
sudo reboot
