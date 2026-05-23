"""Mode registry.

The shell (app.py) doesn't import individual mode classes - it iterates
this registry in the order set by config.MODE_ORDER. Adding a third mode
(e.g. diagnostic, settings) is then a one-file drop-in: write the class,
register it here.

Each mode is a tkinter Frame subclass with one extra method:
    on_show()   - called every time the shell makes this mode active
    on_hide()   - called every time the shell swaps it out
    handle_input(text) - called when the shared bottom input row sends text
                         while this mode is active
"""

from __future__ import annotations

from typing import Callable

import config


_REGISTRY: dict[str, dict] = {}


def register(name: str, label: str, factory: Callable) -> None:
    """`factory(shell)` must return a Frame instance bound to `shell.body`."""
    _REGISTRY[name] = {"label": label, "factory": factory}


def get(name: str) -> dict:
    return _REGISTRY[name]


def names() -> list[str]:
    return [n for n in config.MODE_ORDER if n in _REGISTRY]


def load_all() -> None:
    """Import all mode modules so they self-register. Called by app.py."""
    from . import switching   # noqa: F401
    from . import conversational  # noqa: F401
