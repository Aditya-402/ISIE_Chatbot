"""EV Lab dashboard - multi-mode shell.

Full-screen 800x480 Tk window for the Raspberry Pi 5 + 7" touch panel.
Owns the title bar, mode-toggle strip, and shared bottom input row.
Mode views are pluggable - see modes/__init__.py.

Press ESC to exit (dev convenience).
"""

from __future__ import annotations

import platform
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

import config
import hardware
import voice
import modes
from rag_core import RAGEngine


class AppShell:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(config.APP_TITLE)
        self.root.geometry(config.APP_WINDOW_SIZE)
        self.root.minsize(720, 440)
        self.root.configure(bg=config.COLOUR_BG)
        if config.APP_FULLSCREEN:
            try:
                self.root.attributes("-fullscreen", True)
            except tk.TclError:
                pass
        self.root.bind("<Escape>", lambda _e: self._quit())
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        # Async pipes between worker threads and the Tk main loop.
        self._mic_queue: queue.Queue = queue.Queue()

        # Shared services. Modes read these as needed.
        self.rag: Optional[RAGEngine] = None
        self.stt_backends: dict = {}
        self.tts_backends: dict = {}
        self._mic = voice.MicRecorder()
        self._is_recording = False

        # Mode state.
        self.current_mode_name: Optional[str] = None
        self.mode_views: dict[str, tk.Frame] = {}

        modes.load_all()
        self._build_chrome()
        self._build_modes()
        self.switch_to(config.DEFAULT_MODE)
        self._poll_mic_queue()

        threading.Thread(target=self._load_backends, daemon=True).start()

    # --- chrome ----------------------------------------------------------

    def _build_chrome(self):
        # Top title bar.
        self.title_bar = tk.Frame(self.root, bg=config.COLOUR_TITLE_OFF, height=40)
        self.title_bar.pack(side=tk.TOP, fill=tk.X)
        self.title_bar.pack_propagate(False)
        self.title_lbl = tk.Label(
            self.title_bar, text=config.APP_TITLE,
            bg=config.COLOUR_TITLE_OFF, fg=config.COLOUR_TITLE_FG,
            font=(config.UI_FONT_FAMILY, 14, "bold"),
        )
        self.title_lbl.pack(side=tk.LEFT, padx=16, pady=6)
        self.status_lbl = tk.Label(
            self.title_bar, text="loading engine ...",
            bg=config.COLOUR_TITLE_OFF, fg="#d0d4e0",
            font=(config.UI_FONT_FAMILY, 9),
        )
        self.status_lbl.pack(side=tk.RIGHT, padx=16, pady=6)

        # Bottom input row (TYPING + MIC + SEND).
        self.input_row = tk.Frame(self.root, bg="#1a2542", height=50)
        self.input_row.pack(side=tk.BOTTOM, fill=tk.X)
        self.input_row.pack_propagate(False)

        self.entry = tk.Text(
            self.input_row, height=1, font=(config.UI_FONT_FAMILY, 11),
            wrap=tk.WORD, bg="#ffffff", fg="#0e1b3a",
            relief=tk.FLAT, padx=8, pady=6,
        )
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 4), pady=8)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Shift-Return>", lambda _e: None)

        self.mic_btn = tk.Button(
            self.input_row, text="MIC",
            bg=config.COLOUR_BTN_BG, fg=config.COLOUR_BTN_FG,
            activebackground=config.COLOUR_BTN_ACTIVE,
            font=(config.UI_FONT_FAMILY, 10, "bold"),
            relief=tk.RAISED, bd=2, width=6,
        )
        self.mic_btn.pack(side=tk.LEFT, padx=2, pady=8)
        self.mic_btn.bind("<ButtonPress-1>",   self._on_mic_down)
        self.mic_btn.bind("<ButtonRelease-1>", self._on_mic_up)

        self.send_btn = tk.Button(
            self.input_row, text="SEND",
            bg=config.COLOUR_BTN_BG, fg=config.COLOUR_BTN_FG,
            activebackground=config.COLOUR_BTN_ACTIVE,
            font=(config.UI_FONT_FAMILY, 10, "bold"),
            relief=tk.RAISED, bd=2, width=6,
            command=self._on_send,
        )
        self.send_btn.pack(side=tk.LEFT, padx=(2, 8), pady=8)

        # Vertical BOT toggle strip on the right edge.
        self.toggle_strip = tk.Frame(self.root, bg="#1a2542", width=52)
        self.toggle_strip.pack(side=tk.RIGHT, fill=tk.Y)
        self.toggle_strip.pack_propagate(False)

        self.toggle_btn = tk.Button(
            self.toggle_strip,
            text="B\nO\nT",
            bg=config.COLOUR_BTN_BG, fg=config.COLOUR_BTN_FG,
            activebackground=config.COLOUR_BTN_ACTIVE,
            font=(config.UI_FONT_FAMILY, 14, "bold"),
            relief=tk.RAISED, bd=2,
            command=self._cycle_mode,
        )
        self.toggle_btn.pack(fill=tk.BOTH, expand=True, padx=4, pady=8)

        # Body - mode views are packed into this.
        self.body = tk.Frame(self.root, bg=config.COLOUR_BG)
        self.body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # --- modes -----------------------------------------------------------

    def _build_modes(self):
        for name in modes.names():
            entry = modes.get(name)
            view = entry["factory"](self)
            self.mode_views[name] = view

    def switch_to(self, name: str, **kwargs):
        if name not in self.mode_views:
            return
        if self.current_mode_name == name:
            return
        if self.current_mode_name:
            cur = self.mode_views[self.current_mode_name]
            cur.on_hide()
            cur.pack_forget()
        self.current_mode_name = name
        new = self.mode_views[name]
        new.pack(fill=tk.BOTH, expand=True)
        new.on_show()
        # Title text per mode.
        if name == "conversational":
            self.title_lbl.configure(text=config.APP_TITLE_CHAT)
        else:
            self.title_lbl.configure(text=config.APP_TITLE)
        # Conversational mode can be primed with an initial question
        # forwarded from another mode's input.
        initial = kwargs.get("initial_question")
        if initial and hasattr(new, "handle_input"):
            self.root.after(50, lambda: new.handle_input(initial))

    def _cycle_mode(self):
        order = modes.names()
        if not order:
            return
        idx = order.index(self.current_mode_name) if self.current_mode_name in order else 0
        nxt = order[(idx + 1) % len(order)]
        self.switch_to(nxt)

    # --- title-bar / status --------------------------------------------

    def set_ignition(self, on: bool):
        colour = config.COLOUR_TITLE_ON if on else config.COLOUR_TITLE_OFF
        self.title_bar.configure(bg=colour)
        self.title_lbl.configure(bg=colour)
        self.status_lbl.configure(bg=colour)

    def set_status(self, text: str, fg: str = "#d0d4e0"):
        self.status_lbl.configure(text=text, fg=fg)

    # --- shared input row -----------------------------------------------

    def _on_enter(self, event):
        if event.state & 0x0001:
            return None
        self._on_send()
        return "break"

    def _on_send(self):
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return
        self.entry.delete("1.0", tk.END)
        view = self.mode_views.get(self.current_mode_name)
        if view and hasattr(view, "handle_input"):
            view.handle_input(text)

    # --- mic / push-to-talk ---------------------------------------------

    def _on_mic_down(self, _event):
        if self._is_recording or not self._mic.available:
            return
        self._is_recording = True
        self._mic.start()
        self.mic_btn.configure(text="REC", bg=config.COLOUR_BTN_ACTIVE)
        self.set_status("recording", "#fbbf24")

    def _on_mic_up(self, _event):
        if not self._is_recording:
            return
        self._is_recording = False
        pcm = self._mic.stop()
        self.mic_btn.configure(text="MIC", bg=config.COLOUR_BTN_BG)
        self.set_status("transcribing", "#93c5fd")
        threading.Thread(target=self._transcribe_worker, args=(pcm,),
                         daemon=True).start()

    def _transcribe_worker(self, pcm: bytes):
        backend = self.stt_backends.get(config.DEFAULT_STT)
        if not backend or not backend.available or not pcm:
            text = ""
        else:
            try:
                text = backend.transcribe(pcm)
            except Exception as e:
                self._mic_queue.put(("error", f"STT failed: {e}"))
                return
        self._mic_queue.put(("transcript", text))

    def _poll_mic_queue(self):
        try:
            while True:
                kind, payload = self._mic_queue.get_nowait()
                if kind == "transcript":
                    if payload:
                        view = self.mode_views.get(self.current_mode_name)
                        if view and hasattr(view, "handle_input"):
                            view.handle_input(payload)
                    self.set_status("ready", "#86efac")
                elif kind == "error":
                    self.set_status(payload, "#fca5a5")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_mic_queue)

    # --- backend warm-up ------------------------------------------------

    def _load_backends(self):
        try:
            self.rag = RAGEngine()
        except Exception as e:
            self._mic_queue.put(("error", f"engine load failed: {e}"))
            return
        self.stt_backends = voice.build_stt_backends()
        self.tts_backends = voice.build_tts_backends()
        self._mic_queue.put(("transcript", ""))  # triggers status -> ready
        if not self._mic.available:
            self.mic_btn.configure(state=tk.DISABLED, text="-")

    # --- shutdown -------------------------------------------------------

    def _quit(self):
        try:
            hardware.shutdown()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    AppShell(root)
    root.mainloop()


if __name__ == "__main__":
    main()
