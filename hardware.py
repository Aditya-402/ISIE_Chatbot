"""GPIO control via gpiozero — one output device per signal.

Each signal is a gpiozero DigitalOutputDevice (same family as LED, but it also
handles active-low relays). Pins are addressed by their PHYSICAL board number
via gpiozero's "BOARDnn" spec, so the code reads exactly like the pin you wire
to (the outer / even-numbered column).

The turn-indicator lamps have a HARDWARE flasher, so we drive them STEADY ON
and let the hardware do the blinking — no software blink here. The horn beep
is the one timed pattern we generate (beep()).

Windows (dev): SIM_MODE is on and calls just print. Pi: the real pins drive.

Public API used by server.py:
    set(channel, on)                  - drive a channel ON / OFF
    beep(channel, on_s, gap_s, count) - one-shot beep pattern (horn)
    shutdown()                        - release all devices
"""

from __future__ import annotations

import config

# ===========================================================================
#  PIN MAP   —   signal  ->  PHYSICAL BOARD PIN  (outer / even-numbered column)
# ---------------------------------------------------------------------------
#   "BOARD12" means physical pin 12 on the 40-pin header (not BCM), so what you
#   read here is exactly what you plug into. (BCM GPIO in the comment for ref.)
# ===========================================================================
PIN_MAP: dict[str, str] = {
    "ignition":  "BOARD12",   # GPIO18
    "headlight": "BOARD16",   # GPIO23
    "horn":      "BOARD18",   # GPIO24
    "left_ind":  "BOARD22",   # GPIO25
    "right_ind": "BOARD32",   # GPIO12
    "brake":     "BOARD36",   # GPIO16
    "reverse":   "BOARD40",   # GPIO21
}

# Relays are active-LOW by default (ON when the pin goes LOW). List a channel
# here ONLY if its relay switches ON on HIGH.
ACTIVE_HIGH: dict[str, bool] = {}
# ===========================================================================


def _log(msg: str) -> None:
    print(f"[hardware] {msg}")


if config.SIM_MODE:
    _dev: dict = {}                       # Windows dev: no GPIO, log only
    _log(f"SIM mode — {len(PIN_MAP)} signals will log only.")
else:
    from gpiozero import DigitalOutputDevice
    _dev = {
        ch: DigitalOutputDevice(pin, active_high=ACTIVE_HIGH.get(ch, False),
                                initial_value=False)
        for ch, pin in PIN_MAP.items()
    }
    _log(f"gpiozero ready: {PIN_MAP}")


def set(channel: str, on: bool) -> None:
    """Drive a channel ON or OFF."""
    dev = _dev.get(channel)
    if dev is not None:
        dev.on() if on else dev.off()
    elif config.SIM_MODE:
        _log(f"{channel:<10s} -> {'ON' if on else 'OFF'}")


def beep(channel: str, on_s: float, gap_s: float, count: int) -> None:
    """One-shot: ON on_s / OFF gap_s, repeated `count` times, then OFF (horn).
    gpiozero runs the pattern in the background."""
    dev = _dev.get(channel)
    if dev is not None:
        dev.blink(on_time=on_s, off_time=gap_s, n=count, background=True)
    elif config.SIM_MODE:
        _log(f"{channel:<10s} beep x{count} ({on_s}s on / {gap_s}s gap)")


def shutdown() -> None:
    """Release every device (drives OFF and frees the pins)."""
    for dev in _dev.values():
        dev.off()
        dev.close()
    _dev.clear()
