"""GPIO control via gpiozero — one output device per signal.

Each signal is a gpiozero DigitalOutputDevice (same family as LED, but it also
handles active-low relays and has a built-in .blink()). Pins are addressed by
their PHYSICAL board number via gpiozero's "BOARDnn" spec, so the code reads
exactly like the pin you wire to (the outer / even-numbered column).

Windows (dev): SIM_MODE is on and calls just print. Pi: the real pins drive.
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


_blinking: set[str] = set()

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
    """Drive a channel ON or OFF (cancels any blink running on it)."""
    _blinking.discard(channel)
    dev = _dev.get(channel)
    if dev is not None:
        dev.on() if on else dev.off()
    elif config.SIM_MODE:
        _log(f"{channel:<10s} -> {'ON' if on else 'OFF'}")


def blink(channel: str, period_s: float = None) -> None:
    """Blink continuously: ON half the cycle, OFF the other half. Idempotent."""
    if channel in _blinking:
        return
    half = (period_s or config.INDICATOR_BLINK_PERIOD_S) / 2.0
    _blinking.add(channel)
    dev = _dev.get(channel)
    if dev is not None:
        dev.blink(on_time=half, off_time=half, background=True)
    elif config.SIM_MODE:
        _log(f"{channel:<10s} blink ({half * 2}s cycle)")


def stop_blink(channel: str) -> None:
    """Stop a blink and drive the channel OFF."""
    set(channel, False)


def beep(channel: str, on_s: float, gap_s: float, count: int) -> None:
    """One-shot: ON on_s / OFF gap_s, repeated `count` times, then OFF (horn)."""
    _blinking.discard(channel)
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
    _blinking.clear()
