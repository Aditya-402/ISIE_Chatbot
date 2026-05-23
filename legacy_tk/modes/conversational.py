"""Conversational mode - full-screen chat view.

Reuses RAGEngine + voice backends owned by the shell so warm-up happens
once across the lifetime of the app.

UI here is intentionally minimal: scrollable history + sources. The shared
input row at the bottom of the shell drives questions in via handle_input().
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

import config
from . import register


class ConversationalMode(tk.Frame):

    def __init__(self, shell):
        super().__init__(shell.body, bg="#fbfbfd")
        self.shell = shell
        self._answer_queue: queue.Queue = queue.Queue()
        self._busy = False
        self._build()
        self._poll()

    def _build(self):
        font_base  = (config.UI_FONT_FAMILY, config.UI_FONT_SIZE)
        font_bold  = (config.UI_FONT_FAMILY, config.UI_FONT_SIZE, "bold")
        font_small = (config.UI_FONT_FAMILY, config.UI_FONT_SIZE - 2)

        self.chat = scrolledtext.ScrolledText(
            self, wrap=tk.WORD, state=tk.DISABLED,
            font=font_base, padx=10, pady=8, background="#fbfbfd",
            relief=tk.FLAT, bd=0,
        )
        self.chat.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        self.chat.tag_configure("user",    foreground="#0b5394",
                                font=font_bold, spacing3=4)
        self.chat.tag_configure("bot",     foreground="#0f172a",
                                font=font_base, spacing3=2)
        self.chat.tag_configure("refuse",  foreground="#7c2d12",
                                font=font_bold, spacing3=4)
        self.chat.tag_configure("sources", foreground="#475569",
                                font=font_small, lmargin1=24, lmargin2=24,
                                spacing3=8)
        self.chat.tag_configure("meta",    foreground="#94a3b8",
                                font=font_small, spacing3=8)

        self._print("Bot: Hi! I'm the EV Lab Chatbot. Ask me anything about "
                    "electric vehicles. The first answer takes ~30 s while "
                    "I warm up.\n", "bot")

    # --- shell hooks -----------------------------------------------------

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def handle_input(self, text: str):
        text = text.strip()
        if not text:
            return
        rag = self.shell.rag
        if rag is None:
            self._print("Bot: still warming up, try again in a few seconds.\n",
                        "meta")
            return
        if self._busy:
            self._print("Bot: still answering the last one, please wait.\n",
                        "meta")
            return
        self._print(f"You: {text}\n", "user")
        self._print("...thinking...\n", "meta")
        self._busy = True
        self.shell.set_status("thinking", "#b45309")
        threading.Thread(target=self._worker, args=(text, rag),
                         daemon=True).start()

    # --- workers ---------------------------------------------------------

    def _worker(self, question: str, rag):
        try:
            out = rag.answer(question)
        except Exception as e:
            out = {"text": f"(error: {e})", "sources": [], "refused": False,
                   "latency": 0, "gate_reason": None,
                   "top_dense_score": None, "top_rrf_score": None}
        self._answer_queue.put(out)

    def _poll(self):
        try:
            while True:
                out = self._answer_queue.get_nowait()
                self._render_answer(out)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll)

    def _render_answer(self, out: dict):
        # Drop the "...thinking..." line.
        self.chat.configure(state=tk.NORMAL)
        try:
            self.chat.delete("end-2l linestart", "end-1c")
        except tk.TclError:
            pass
        self.chat.configure(state=tk.DISABLED)

        tag = "refuse" if out["refused"] else "bot"
        prefix = "Bot (refused): " if out["refused"] else "Bot: "
        self._print(f"{prefix}{out['text']}\n", tag)
        self._print_sources(out.get("sources", []))

        meta_parts = [f"{out['latency']}s"]
        if out.get("top_dense_score") is not None:
            meta_parts.append(f"dense={out['top_dense_score']:.2f}")
        if out.get("gate_reason"):
            meta_parts.append(f"gate: {out['gate_reason']}")
        self._print(f"({' • '.join(meta_parts)})\n", "meta")

        self._busy = False
        self.shell.set_status("ready", "#15803d")

        if out.get("text"):
            tts = self.shell.tts_backends.get(config.DEFAULT_TTS)
            if tts and tts.available:
                threading.Thread(target=tts.speak, args=(out["text"],),
                                 daemon=True).start()

    # --- helpers ---------------------------------------------------------

    def _print(self, text: str, tag: str):
        self.chat.configure(state=tk.NORMAL)
        self.chat.insert(tk.END, text, tag)
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _print_sources(self, sources: list[dict]):
        if not sources:
            return
        lines = ["Sources:"]
        seen = set()
        for s in sources:
            k = (s["source"], s["page"])
            if k in seen:
                continue
            seen.add(k)
            lines.append(f"  • {s['source']}, p.{s['page']}")
        self._print("\n".join(lines) + "\n", "sources")


register("conversational", "Chat", lambda shell: ConversationalMode(shell))
