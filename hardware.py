"""Tiny GPIO abstraction so the UI is identical on Windows (sim) and Pi (real).

Channels are referred to by logical name (e.g. "headlight", "left_ind") to
keep the UI decoupled from pin numbers. When wiring is decided, fill in
PIN_MAP and implement the real-mode branch.

Public surface:
    set(channel, on)        - drive channel high/low
    pulse_on(channel)       - momentary press start (horn / brake)
    pulse_off(channel)
    blink(channel, hz)      - software blink at given rate
    stop_blink(channel)
    shutdown()              - stop blinkers, release GPIO

In SIM_MODE every call just prints; nothing else changes.
"""

from __future__ import annotations

import threading
import time

import config


# --- Pin map (placeholder - fill in once wiring is chosen) ---------------
# Active-low vs active-high also TBD; flip ACTIVE_HIGH per channel if mixed.

PIN_MAP: dict[str, int] = {
    # channel        BCM   (physical pin — OUTER row, even-numbered pins only)
    "ignition":      18,   # pin 12  (hardware signal AND master gate)
    "headlight":     23,   # pin 16
    "horn":          24,   # pin 18
    "left_ind":      25,   # pin 22
    "right_ind":     12,   # pin 32
    "brake":         16,   # pin 36  (tail lamp)
    # SOFTWARE-ONLY signals (no GPIO pin of their own):
    #   hazard    -> blinks left_ind + right_ind together
    #   all_lamp  -> forces headlight + brake on and blinks both indicators
    # UI-only until wired: reverse, parking_brake
    # Relay-board GND: use an even-row ground pin (6, 14, 20, 30 or 34).
    # Skipped even pins: 2/4 (5V), 6/14/20/30/34 (GND), 8/10 (UART),
    # 24/26 (SPI CE), 28 (HAT ID EEPROM).
}

# Active-LOW is the default (most relay boards switch ON when the pin goes
# LOW). Only list a channel here if its relay/driver switches ON on HIGH.
ACTIVE_HIGH: dict[str, bool] = {
    # "horn": True,
}


# --- Implementation switch ------------------------------------------------

_gpio = None        # set to RPi.GPIO module in real mode
_pin_state: dict[str, bool] = {c: False for c in config.CONTROL_CHANNELS}
_blink_threads: dict[str, threading.Event] = {}
_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[hardware] {msg}")


def _init_real() -> None:
    """Bind to the GPIO library when running on the Pi. Called lazily on the
    first set().

    NOTE for Raspberry Pi 5: the classic `RPi.GPIO` does NOT work on the Pi
    5's RP1 I/O controller. Install the drop-in `rpi-lgpio` instead — it
    exposes the same `import RPi.GPIO as GPIO` API on top of lgpio. See
    requirements-pi.txt. The import line below is unchanged either way.
    """
    global _gpio
    if _gpio is not None:
        return
    try:
        import RPi.GPIO as GPIO  # type: ignore
    except Exception as e:
        _log(f"GPIO library unavailable ({e}); staying in sim prints. "
             f"On a Raspberry Pi 5 run:  pip3 install -r requirements-pi.txt")
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for channel, pin in PIN_MAP.items():
        # Start each pin in its OFF state, honouring per-channel polarity so
        # active-high relays don't fire on boot.
        off_level = GPIO.LOW if ACTIVE_HIGH.get(channel, False) else GPIO.HIGH
        GPIO.setup(pin, GPIO.OUT, initial=off_level)
    _gpio = GPIO
    _log(f"GPIO ready (BCM pins): {PIN_MAP}")


def _write(channel: str, on: bool) -> None:
    """Drive the underlying pin. No-op in sim mode."""
    if config.SIM_MODE:
        return
    if _gpio is None:
        _init_real()
    if _gpio is None or channel not in PIN_MAP:
        return
    pin = PIN_MAP[channel]
    high_means_on = ACTIVE_HIGH.get(channel, False)
    level = _gpio.HIGH if (on == high_means_on) else _gpio.LOW
    _gpio.output(pin, level)


# --- Public API -----------------------------------------------------------

def set(channel: str, on: bool) -> None:
    """Latch a channel ON or OFF."""
    with _lock:
        prev = _pin_state.get(channel, False)
        _pin_state[channel] = on
        if prev != on:
            _log(f"{channel:<10s} -> {'ON' if on else 'OFF'}")
        _write(channel, on)


def pulse_on(channel: str) -> None:
    """Begin a momentary press (horn / brake)."""
    set(channel, True)


def pulse_off(channel: str) -> None:
    """End a momentary press."""
    set(channel, False)


def state(channel: str) -> bool:
    return _pin_state.get(channel, False)


def blink(channel: str, period_s: float = None) -> None:
    """Start a software blinker on the given channel.

    `period_s` is the full ON+OFF cycle in seconds (default from config);
    the lamp is on for half of it and off for the other half.
    Idempotent: calling on an already-blinking channel is a no-op.
    """
    period_s = period_s or config.INDICATOR_BLINK_PERIOD_S
    with _lock:
        if channel in _blink_threads:
            return
        stop = threading.Event()
        _blink_threads[channel] = stop

    half = max(period_s, 0.2) / 2.0   # on-duration = off-duration

    def _loop():
        on = True
        while not stop.is_set():
            set(channel, on)
            on = not on
            stop.wait(half)
        set(channel, False)

    threading.Thread(target=_loop, daemon=True, name=f"blink-{channel}").start()
    _log(f"{channel:<10s} blink start ({period_s}s cycle)")


def stop_blink(channel: str) -> None:
    with _lock:
        stop = _blink_threads.pop(channel, None)
    if stop:
        stop.set()
        _log(f"{channel:<10s} blink stop")


def pulse_sequence(channel: str, steps: list[tuple[bool, float]]) -> None:
    """Run a one-shot ON/OFF pattern in a worker thread, then leave the
    channel OFF. `steps` is a list of (on, seconds). Re-triggering cancels the
    previous run; registered in _blink_threads so stop_blink() (and the
    ignition gate) cancels it too. Used for the horn beep pattern.
    """
    stop_blink(channel)              # cancel any running blink/sequence
    stop = threading.Event()
    with _lock:
        _blink_threads[channel] = stop

    def _run():
        for on, secs in steps:
            if stop.is_set():
                break
            set(channel, on)
            stop.wait(max(secs, 0.0))
        set(channel, False)
        with _lock:
            if _blink_threads.get(channel) is stop:
                _blink_threads.pop(channel, None)

    threading.Thread(target=_run, daemon=True, name=f"seq-{channel}").start()
    _log(f"{channel:<10s} sequence start ({len(steps)} steps)")


def shutdown() -> None:
    """Stop all blinkers and release GPIO."""
    with _lock:
        threads = list(_blink_threads.values())
        _blink_threads.clear()
    for stop in threads:
        stop.set()
    if _gpio is not None:
        try:
            _gpio.cleanup()
        except Exception:
            pass


# --- Boot banner ----------------------------------------------------------

_log(f"hardware module loaded in {'SIM' if config.SIM_MODE else 'REAL'} mode "
     f"({len(PIN_MAP)} pins mapped)")
