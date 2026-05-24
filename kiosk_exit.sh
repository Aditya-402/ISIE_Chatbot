#!/usr/bin/env bash
# Exit the kiosk to the desktop: tell the relaunch loop to stop, then close
# Chromium. Bound to Ctrl+Alt+X in labwc's rc.xml by kiosk_setup.sh.
# (The dashboard server keeps running; SSH is always available for maintenance.)
touch /tmp/ev_kiosk_stop
pkill chromium 2>/dev/null || true
