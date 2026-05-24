#!/usr/bin/env bash
# Launch the EV Lab dashboard in Chromium kiosk (fullscreen), relaunching if it
# exits or crashes. Started by ~/.config/labwc/autostart (written by kiosk_setup.sh).
# Ctrl+Alt+X -> kiosk_exit.sh stops the loop and drops to the desktop.
set -u
URL="http://127.0.0.1:8000"
PROFILE="$HOME/.config/ev-kiosk-chrome"
rm -f /tmp/ev_kiosk_stop

# Wait for the server to answer (it warms up for ~10-30 s after boot).
for _ in $(seq 1 120); do
  curl -s -o /dev/null "$URL/api/state" && break
  sleep 1
done

while true; do
  chromium \
    --ozone-platform=wayland \
    --kiosk "$URL" \
    --user-data-dir="$PROFILE" \
    --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --disable-features=Translate \
    --use-fake-ui-for-media-stream \
    --autoplay-policy=no-user-gesture-required \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    >/dev/null 2>&1

  # If the exit combo asked us to stop, leave the desktop up; else relaunch.
  if [ -f /tmp/ev_kiosk_stop ]; then rm -f /tmp/ev_kiosk_stop; break; fi
  sleep 2
done
