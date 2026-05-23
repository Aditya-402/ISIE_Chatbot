"""EV Lab Chatbot — Tkinter UI.

Features:
  - Scrollable chat history with styled user / bot / refusal messages
  - Per-answer sources panel (PDF + page) under each bot turn
  - Text input + Send (Enter to send, Shift+Enter for newline)
  - Push-to-talk Mic button (hold to record, release to send)
  - Radio buttons to switch STT  (Vosk offline / Google online)
  - Radio buttons to switch TTS  (pyttsx3 offline / gTTS online)
  - Worker threads keep the UI responsive during the ~55 s LLM call
    and the few-second TTS playback.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import Optional

import config
import voice
from rag_core import RAGEngine


# ---------------------------------------------------------------------------

class ChatbotUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(config.UI_TITLE)
        self.root.geometry(config.UI_WINDOW_SIZE)
        self.root.minsize(640, 520)

        # Worker pipes
        self._answer_queue: queue.Queue = queue.Queue()
        self._mic = voice.MicRecorder()
        self._is_recording = False

        # RAG engine + voice backends loaded lazily on a worker so the
        # window appears immediately.
        self.rag: Optional[RAGEngine] = None
        self.stt_backends: dict = {}
        self.tts_backends: dict = {}

        self._build_widgets()
        self._poll_answer_queue()

        threading.Thread(target=self._load_backends, daemon=True).start()

    # --- widget layout ------------------------------------------------------

    def _build_widgets(self):
        font_base = (config.UI_FONT_FAMILY, config.UI_FONT_SIZE)
        font_bold = (config.UI_FONT_FAMILY, config.UI_FONT_SIZE, "bold")
        font_small = (config.UI_FONT_FAMILY, config.UI_FONT_SIZE - 2)

        # --- top control bar: backend radios + status -----------------------
        controls = ttk.Frame(self.root, padding=(8, 6))
        controls.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(controls, text="STT:", font=font_small).pack(side=tk.LEFT)
        self.stt_var = tk.StringVar(value=config.DEFAULT_STT)
        self.stt_vosk_rb = ttk.Radiobutton(controls, text="Vosk (offline)",
                                           variable=self.stt_var, value="vosk")
        self.stt_vosk_rb.pack(side=tk.LEFT, padx=(4, 8))
        self.stt_google_rb = ttk.Radiobutton(controls, text="Google (online)",
                                             variable=self.stt_var, value="google")
        self.stt_google_rb.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(controls, text="TTS:", font=font_small).pack(side=tk.LEFT)
        self.tts_var = tk.StringVar(value=config.DEFAULT_TTS)
        self.tts_p_rb = ttk.Radiobutton(controls, text="pyttsx3 (offline)",
                                        variable=self.tts_var, value="pyttsx3")
        self.tts_p_rb.pack(side=tk.LEFT, padx=(4, 8))
        self.tts_g_rb = ttk.Radiobutton(controls, text="gTTS (online)",
                                        variable=self.tts_var, value="gtts")
        self.tts_g_rb.pack(side=tk.LEFT, padx=(0, 16))

        self.speak_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Speak answers",
                        variable=self.speak_var).pack(side=tk.LEFT, padx=(0, 16))

        self.status_lbl = ttk.Label(controls, text="loading engine ...",
                                    font=font_small, foreground="#666")
        self.status_lbl.pack(side=tk.RIGHT)

        # --- chat history (read-only ScrolledText) --------------------------
        self.chat = scrolledtext.ScrolledText(
            self.root, wrap=tk.WORD, state=tk.DISABLED,
            font=font_base, padx=10, pady=8, background="#fbfbfd",
        )
        self.chat.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 0))

        self.chat.tag_configure("user",   foreground="#0b5394",
                                font=font_bold, spacing3=4)
        self.chat.tag_configure("bot",    foreground="#0f172a",
                                font=font_base, spacing3=2)
        self.chat.tag_configure("refuse", foreground="#7c2d12",
                                font=font_bold, spacing3=4)
        self.chat.tag_configure("sources", foreground="#475569",
                                font=font_small, lmargin1=24, lmargin2=24,
                                spacing3=8)
        self.chat.tag_configure("meta",   foreground="#94a3b8",
                                font=font_small, spacing3=8)

        # --- input row: text entry + send + mic -----------------------------
        inrow = ttk.Frame(self.root, padding=(8, 8))
        inrow.pack(side=tk.BOTTOM, fill=tk.X)

        self.entry = tk.Text(inrow, height=2, font=font_base, wrap=tk.WORD)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Shift-Return>", lambda e: None)  # let newline through

        self.send_btn = ttk.Button(inrow, text="Send", command=self._on_send)
        self.send_btn.pack(side=tk.LEFT, padx=2)

        # push-to-talk
        self.mic_btn = ttk.Button(inrow, text="🎤 Hold to talk")
        self.mic_btn.pack(side=tk.LEFT, padx=2)
        self.mic_btn.bind("<ButtonPress-1>",   self._on_mic_down)
        self.mic_btn.bind("<ButtonRelease-1>", self._on_mic_up)

        # initial banner
        self._print(
            "Bot: Hi! I'm the EV Lab Chatbot. Ask me anything about "
            "electric vehicles. The first answer takes ~30 s while I warm up.\n",
            "bot")

    # --- background load ---------------------------------------------------

    def _load_backends(self):
        try:
            self.rag = RAGEngine()
        except Exception as e:
            self._answer_queue.put(("__error__", f"engine load failed: {e}"))
            return
        self.stt_backends = voice.build_stt_backends()
        self.tts_backends = voice.build_tts_backends()
        self._answer_queue.put(("__ready__", None))

    def _on_ready(self):
        # Disable radio buttons whose backend failed to load.
        for name, rb in [("vosk", self.stt_vosk_rb),
                         ("google", self.stt_google_rb)]:
            b = self.stt_backends.get(name)
            if not b or not b.available:
                rb.state(["disabled"])
                if not self._mic.available and rb is self.stt_vosk_rb:
                    pass  # mic itself is broken, separate problem
        for name, rb in [("pyttsx3", self.tts_p_rb),
                         ("gtts", self.tts_g_rb)]:
            b = self.tts_backends.get(name)
            if not b or not b.available:
                rb.state(["disabled"])
        if not self._mic.available:
            self.mic_btn.state(["disabled"])
            self.mic_btn.configure(text="🎤 (no mic)")
        self.status_lbl.configure(text="ready",  foreground="#15803d")

    # --- chat helpers -------------------------------------------------------

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

    # --- send / receive -----------------------------------------------------

    def _on_enter(self, event):
        # Send unless Shift was held (Tkinter delivers <Shift-Return> first)
        if event.state & 0x0001:
            return None
        self._on_send()
        return "break"

    def _on_send(self):
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return
        self.entry.delete("1.0", tk.END)
        if not self.rag:
            self._print("Bot: still warming up, try again in a few seconds.\n", "meta")
            return
        self._print(f"You: {text}\n", "user")
        self._print("...thinking...\n", "meta")
        self.send_btn.state(["disabled"])
        threading.Thread(target=self._answer_worker, args=(text,), daemon=True).start()

    def _answer_worker(self, question: str):
        try:
            out = self.rag.answer(question)
        except Exception as e:
            out = {"text": f"(error: {e})", "sources": [], "refused": False,
                   "latency": 0, "gate_reason": None,
                   "top_dense_score": None, "top_rrf_score": None}
        self._answer_queue.put(("answer", out))

    def _poll_answer_queue(self):
        try:
            while True:
                kind, payload = self._answer_queue.get_nowait()
                if kind == "__ready__":
                    self._on_ready()
                elif kind == "__error__":
                    self._print(f"Bot: {payload}\n", "refuse")
                    self.status_lbl.configure(text="error", foreground="#b91c1c")
                elif kind == "answer":
                    self._handle_answer(payload)
                elif kind == "transcript":
                    if payload:
                        self.entry.delete("1.0", tk.END)
                        self.entry.insert("1.0", payload)
                        self._on_send()
                    else:
                        self._print("Bot: (didn't catch that)\n", "meta")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_answer_queue)

    def _handle_answer(self, out: dict):
        # Strip the trailing "...thinking..." line
        self.chat.configure(state=tk.NORMAL)
        self.chat.delete("end-2l linestart", "end-1c")
        self.chat.configure(state=tk.DISABLED)

        tag = "refuse" if out["refused"] else "bot"
        prefix = "Bot (refused): " if out["refused"] else "Bot: "
        self._print(f"{prefix}{out['text']}\n", tag)
        self._print_sources(out["sources"])

        meta = (f"({out['latency']}s"
                + (f" • dense={out['top_dense_score']:.2f}"
                   if out.get("top_dense_score") is not None else "")
                + (f" • gate: {out['gate_reason']}" if out.get("gate_reason") else "")
                + ")\n")
        self._print(meta, "meta")

        self.send_btn.state(["!disabled"])

        if self.speak_var.get() and out["text"]:
            tts = self.tts_backends.get(self.tts_var.get())
            if tts and tts.available:
                threading.Thread(target=tts.speak, args=(out["text"],),
                                 daemon=True).start()

    # --- mic / push-to-talk ------------------------------------------------

    def _on_mic_down(self, _event):
        if self._is_recording or not self._mic.available:
            return
        self._is_recording = True
        self._mic.start()
        self.mic_btn.configure(text="🎤 recording…")
        self.status_lbl.configure(text="recording", foreground="#b45309")

    def _on_mic_up(self, _event):
        if not self._is_recording:
            return
        self._is_recording = False
        pcm = self._mic.stop()
        self.mic_btn.configure(text="🎤 Hold to talk")
        self.status_lbl.configure(text="transcribing", foreground="#0369a1")
        threading.Thread(target=self._transcribe_worker, args=(pcm,),
                         daemon=True).start()

    def _transcribe_worker(self, pcm: bytes):
        backend = self.stt_backends.get(self.stt_var.get())
        if not backend or not backend.available or not pcm:
            text = ""
        else:
            try:
                text = backend.transcribe(pcm)
            except Exception as e:
                text = ""
                self._answer_queue.put(("__error__", f"STT failed: {e}"))
                return
        self.status_lbl.configure(text="ready", foreground="#15803d")
        self._answer_queue.put(("transcript", text))


# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    ChatbotUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
