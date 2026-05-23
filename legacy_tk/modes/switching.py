"""Switching mode - IoT EV dashboard with vehicle controls.

Layout (inside the shell's body area, ~750 x 390 on the 7" panel):

    +-----------------------------------------------+
    |  vehicle-state Canvas  (icons, arrows, lanes) |
    |  ~750 x 220                                   |
    +-----------------------------------------------+
    |  Headlight   |  IGNITION  |  Horn             |
    |  All Lamp    |  Reverse   |  Hazard           |   3x3 button grid
    |  L Indicator |  Brake     |  R Indicator      |
    +-----------------------------------------------+

Button behaviour classes:
  * Latched master : Ignition. Disables everything else when OFF.
                     Reports state to shell for title-bar colour.
  * Toggle         : Headlight, All Lamp, Reverse, Hazard, L Ind, R Ind.
  * Momentary      : Horn, Brake. Pressed = active.

Interlocks:
  * L and R Indicator are mutually exclusive.
  * Hazard overrides both indicators while active.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import config
import hardware
from . import register


class SwitchingMode(tk.Frame):

    def __init__(self, shell):
        super().__init__(shell.body, bg=config.COLOUR_BG)
        self.shell = shell

        # Logical state of every toggleable control. Ignition gates the rest.
        self.state = {ch: False for ch in config.CONTROL_CHANNELS}

        # Maps channel -> tk.Button so we can grey out / restyle.
        self.buttons: dict[str, tk.Button] = {}

        # Canvas item ids for icons we light up.
        self._icon_ids: dict[str, list[int]] = {}

        # Blink-mirror flag for arrow indicators (mirrors hardware blink
        # state for the on-screen arrows).
        self._blink_on: dict[str, bool] = {"left_ind": False, "right_ind": False}
        self._blink_after: dict[str, str | None] = {"left_ind": None,
                                                    "right_ind": None}

        self._build()

    # --- layout ----------------------------------------------------------

    def _build(self):
        # Vehicle-state Canvas at the top.
        self.canvas = tk.Canvas(self, bg=config.COLOUR_BG, highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self.canvas.bind("<Configure>", lambda _e: self._draw_state_panel())

        # 3x3 control grid.
        grid = tk.Frame(self, bg=config.COLOUR_BG)
        grid.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 6))
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="ctl")

        layout = [
            # (row, col, channel, label_off, label_on, kind)
            (0, 0, "headlight", "Head light OFF", "Head light ON", "toggle"),
            (0, 1, "ignition",  "IGNITION OFF",   "IGNITION ON",   "master"),
            (0, 2, "horn",      "Horn",           "HORN",          "momentary"),
            (1, 0, "all_lamp",  "All Lamp Mode OFF", "All Lamp Mode ON", "toggle"),
            (1, 1, "reverse",   "Reverse",        "REVERSE",       "toggle"),
            (1, 2, "hazard",    "Hazard Mode OFF", "Hazard Mode ON", "toggle"),
            (2, 0, "left_ind",  "Left Indicator", "LEFT IND",      "toggle"),
            (2, 1, "brake",     "Brake",          "BRAKE",         "momentary"),
            (2, 2, "right_ind", "Right Indicator", "RIGHT IND",    "toggle"),
        ]
        self._labels = {ch: (off, on) for _r, _c, ch, off, on, _k in layout}

        for row, col, ch, off, _on, kind in layout:
            btn = tk.Button(
                grid, text=off,
                bg=config.COLOUR_BTN_BG, fg=config.COLOUR_BTN_FG,
                activebackground=config.COLOUR_BTN_ACTIVE,
                activeforeground=config.COLOUR_BTN_FG,
                font=(config.UI_FONT_FAMILY, 11, "bold"),
                relief=tk.RAISED, bd=2, padx=6, pady=8,
            )
            if kind == "momentary":
                btn.bind("<ButtonPress-1>",   lambda _e, c=ch: self._on_momentary(c, True))
                btn.bind("<ButtonRelease-1>", lambda _e, c=ch: self._on_momentary(c, False))
            elif kind == "master":
                btn.configure(command=lambda c=ch: self._on_ignition())
            else:
                btn.configure(command=lambda c=ch: self._on_toggle(c))
            btn.grid(row=row, column=col, sticky="nsew", padx=4, pady=3)
            self.buttons[ch] = btn

        # Until ignition is on, every other button is disabled.
        self._apply_gating()

    # --- canvas / state graphic ------------------------------------------

    def _draw_state_panel(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50:
            return

        # Rounded dashboard frame (rectangle with arcs at corners).
        pad = 6
        c.create_rectangle(pad, pad, w - pad, h - pad,
                           fill=config.COLOUR_BG, outline="#1a2d5e", width=2)

        # Horizon strip (green when ignition on, dim grey otherwise).
        horizon_colour = "#39d353" if self.state["ignition"] else "#2a3f6f"
        c.create_rectangle(w * 0.18, h * 0.10, w * 0.82, h * 0.14,
                           fill=horizon_colour, outline="")

        # Converging lane lines from bottom-centre.
        cx = w / 2
        lane_y_top = h * 0.18
        lane_y_bot = h * 0.92
        c.create_line(cx - 6, lane_y_top, cx - w * 0.30, lane_y_bot,
                      fill=config.COLOUR_LANE, width=2)
        c.create_line(cx + 6, lane_y_top, cx + w * 0.30, lane_y_bot,
                      fill=config.COLOUR_LANE, width=2)
        # Dashed centre line
        for i in range(4):
            y0 = lane_y_top + (lane_y_bot - lane_y_top) * (i / 4) + 6
            y1 = y0 + (lane_y_bot - lane_y_top) * 0.08
            c.create_line(cx, y0, cx, y1, fill=config.COLOUR_LANE,
                          width=2, dash=(4, 4))

        # Centre wheel circle.
        r = min(w, h) * 0.16
        c.create_oval(cx - r, h * 0.50 - r, cx + r, h * 0.50 + r,
                      outline=config.COLOUR_PANEL_FG, width=2)

        # Arrow indicators (blink mirrors hardware blink state).
        self._draw_left_arrow(c, w, h)
        self._draw_right_arrow(c, w, h)

        # Icon cluster.
        self._draw_headlight_icon(c, w, h)
        self._draw_hazard_icon(c, w, h)
        self._draw_info_icon(c, w, h)
        self._draw_car_icon(c, w, h)

    def _arrow_visible(self, ch: str) -> bool:
        if self.state["hazard"]:
            return self._blink_on[ch]   # both arrows blink in sync
        if self.state[ch]:
            return self._blink_on[ch]
        return False

    def _draw_left_arrow(self, c, w, h):
        col = config.COLOUR_ICON_ON if self._arrow_visible("left_ind") else config.COLOUR_ICON_OFF
        x = w * 0.10
        y = h * 0.18
        c.create_polygon(x, y + 10, x + 24, y, x + 24, y + 6,
                         x + 44, y + 6, x + 44, y + 14,
                         x + 24, y + 14, x + 24, y + 20,
                         fill=col, outline="")

    def _draw_right_arrow(self, c, w, h):
        col = config.COLOUR_ICON_ON if self._arrow_visible("right_ind") else config.COLOUR_ICON_OFF
        x = w * 0.90
        y = h * 0.18
        c.create_polygon(x, y + 10, x - 24, y, x - 24, y + 6,
                         x - 44, y + 6, x - 44, y + 14,
                         x - 24, y + 14, x - 24, y + 20,
                         fill=col, outline="")

    def _draw_headlight_icon(self, c, w, h):
        col = config.COLOUR_ICON_ON if self.state["headlight"] else config.COLOUR_ICON_OFF
        x = w * 0.78
        y = h * 0.42
        c.create_arc(x - 14, y - 14, x + 14, y + 14, start=300, extent=120,
                     style=tk.CHORD, fill=col, outline="")
        for i in range(3):
            c.create_line(x + 16, y - 6 + i * 6, x + 30, y - 6 + i * 6,
                          fill=col, width=2)

    def _draw_hazard_icon(self, c, w, h):
        col = config.COLOUR_HAZARD if self.state["hazard"] else config.COLOUR_ICON_OFF
        x = w * 0.22
        y = h * 0.70
        c.create_polygon(x, y - 16, x + 14, y + 10, x - 14, y + 10,
                         fill=col, outline=config.COLOUR_PANEL_FG, width=1)
        c.create_text(x, y + 2, text="!", fill="#000000",
                      font=(config.UI_FONT_FAMILY, 10, "bold"))

    def _draw_info_icon(self, c, w, h):
        col = config.COLOUR_ICON_ON if self.state["all_lamp"] else config.COLOUR_ICON_OFF
        x = w * 0.78
        y = h * 0.70
        c.create_oval(x - 12, y - 12, x + 12, y + 12, outline=col, width=2)
        c.create_text(x, y, text="i", fill=col,
                      font=(config.UI_FONT_FAMILY, 11, "bold"))

    def _draw_car_icon(self, c, w, h):
        col = config.COLOUR_ICON_ON if self.state["reverse"] else config.COLOUR_ICON_OFF
        x = w * 0.22
        y = h * 0.42
        c.create_rectangle(x - 16, y - 6, x + 16, y + 6, fill=col, outline="")
        c.create_polygon(x - 12, y - 6, x - 6, y - 14, x + 6, y - 14, x + 12, y - 6,
                         fill=col, outline="")

    # --- button handlers -------------------------------------------------

    def _on_ignition(self):
        new = not self.state["ignition"]
        self.state["ignition"] = new
        hardware.set("ignition", new)
        self._restyle("ignition")
        self._apply_gating()
        self.shell.set_ignition(new)   # title bar swap
        if not new:
            # Killing ignition cancels every active state.
            for ch in config.CONTROL_CHANNELS:
                if ch == "ignition":
                    continue
                if self.state.get(ch):
                    self.state[ch] = False
                    hardware.set(ch, False)
                    self._restyle(ch)
                hardware.stop_blink(ch)
                self._cancel_blink_mirror(ch)
        self._draw_state_panel()

    def _on_toggle(self, ch: str):
        if not self.state["ignition"]:
            return
        new = not self.state[ch]
        self.state[ch] = new

        # Indicator interlocks.
        if ch in ("left_ind", "right_ind") and new:
            other = "right_ind" if ch == "left_ind" else "left_ind"
            if self.state[other]:
                self.state[other] = False
                hardware.stop_blink(other)
                self._cancel_blink_mirror(other)
                self._restyle(other)

        if ch == "hazard":
            if new:
                hardware.blink("left_ind")
                hardware.blink("right_ind")
                self._start_blink_mirror("left_ind")
                self._start_blink_mirror("right_ind")
            else:
                # Stop hazard; leave whichever individual indicator is on.
                hardware.stop_blink("left_ind")
                hardware.stop_blink("right_ind")
                self._cancel_blink_mirror("left_ind")
                self._cancel_blink_mirror("right_ind")
                if self.state["left_ind"]:
                    hardware.blink("left_ind")
                    self._start_blink_mirror("left_ind")
                if self.state["right_ind"]:
                    hardware.blink("right_ind")
                    self._start_blink_mirror("right_ind")
        elif ch in ("left_ind", "right_ind"):
            if self.state["hazard"]:
                pass  # hazard already drives both; toggle just records intent
            elif new:
                hardware.blink(ch)
                self._start_blink_mirror(ch)
            else:
                hardware.stop_blink(ch)
                self._cancel_blink_mirror(ch)
        else:
            hardware.set(ch, new)

        self._restyle(ch)
        self._draw_state_panel()

    def _on_momentary(self, ch: str, pressed: bool):
        if not self.state["ignition"]:
            return
        self.state[ch] = pressed
        if pressed:
            hardware.pulse_on(ch)
        else:
            hardware.pulse_off(ch)
        self._restyle(ch)
        self._draw_state_panel()

    # --- on-screen blink mirror -----------------------------------------

    def _start_blink_mirror(self, ch: str):
        if self._blink_after[ch] is not None:
            return
        self._blink_on[ch] = True
        self._tick_blink_mirror(ch)

    def _cancel_blink_mirror(self, ch: str):
        h = self._blink_after.get(ch)
        if h is not None:
            try:
                self.after_cancel(h)
            except Exception:
                pass
        self._blink_after[ch] = None
        self._blink_on[ch] = False

    def _tick_blink_mirror(self, ch: str):
        period_ms = int(500 / max(config.INDICATOR_BLINK_HZ, 0.1))
        self._blink_on[ch] = not self._blink_on[ch]
        self._draw_state_panel()
        self._blink_after[ch] = self.after(period_ms, lambda c=ch: self._tick_blink_mirror(c))

    # --- styling helpers --------------------------------------------------

    def _restyle(self, ch: str):
        btn = self.buttons.get(ch)
        if not btn:
            return
        off_lbl, on_lbl = self._labels[ch]
        on = self.state.get(ch, False)
        btn.configure(
            text=on_lbl if on else off_lbl,
            bg=config.COLOUR_BTN_ACTIVE if on else config.COLOUR_BTN_BG,
            relief=tk.SUNKEN if on else tk.RAISED,
        )

    def _apply_gating(self):
        ign_on = self.state["ignition"]
        for ch, btn in self.buttons.items():
            if ch == "ignition":
                continue
            btn.configure(state=tk.NORMAL if ign_on else tk.DISABLED)
        self._restyle("ignition")

    # --- shell hooks -----------------------------------------------------

    def on_show(self):
        self._draw_state_panel()

    def on_hide(self):
        pass

    def handle_input(self, text: str):
        """Bottom input row sent text while switching mode was active.
        Forward to conversational mode and switch to it."""
        self.shell.switch_to("conversational", initial_question=text)


register("switching", "Dashboard", lambda shell: SwitchingMode(shell))
