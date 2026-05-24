"""STT + TTS backends with a uniform interface so the chatbot UI can swap
between them via radio buttons at runtime.

Backends are imported lazily — if a package isn't installed, the
corresponding class just reports `available=False` and the UI can grey
the radio button out.

STT (speech to text):
  - VoskSTT     offline, bundled model in models/vosk-en/
  - GoogleSTT   online, via SpeechRecognition.recognize_google()
TTS (text to speech):
  - Pyttsx3TTS  offline, uses SAPI5 on Windows / espeak-ng on Linux
  - GTTSTTS     online, via gTTS + pygame.mixer playback
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
from pathlib import Path

import config


# ---------------------------------------------------------------------------
# Microphone capture (used by both STT backends)
# ---------------------------------------------------------------------------

class MicRecorder:
    """Records mono 16 kHz PCM into a list of numpy chunks; start() / stop()
    are non-blocking so the UI can drive push-to-talk."""

    def __init__(self):
        self._frames: list = []
        self._stream = None
        self._sd = None  # sounddevice imported lazily
        self.available = True
        self.error: str | None = None
        try:
            import sounddevice  # noqa: F401
        except Exception as e:
            self.available = False
            self.error = f"sounddevice not available: {e}"

    def start(self):
        if not self.available:
            return
        import sounddevice as sd
        import numpy as np
        self._sd = sd
        self._frames = []

        def _callback(indata, frames, time_info, status):
            self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=config.MIC_SAMPLE_RATE,
            channels=config.MIC_CHANNELS,
            dtype="int16",
            callback=_callback,
        )
        self._stream.start()

    def stop(self) -> bytes:
        """Stop recording and return the raw 16-bit PCM bytes."""
        if not self._stream:
            return b""
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._frames:
            return b""
        import numpy as np
        audio = np.concatenate(self._frames, axis=0)
        return audio.tobytes()


# ---------------------------------------------------------------------------
# STT backends
# ---------------------------------------------------------------------------

class _STTBackend:
    name = "abstract"
    available: bool = False
    error: str | None = None
    def transcribe(self, pcm_bytes: bytes) -> str:
        raise NotImplementedError


class VoskSTT(_STTBackend):
    """Offline. Uses the bundled vosk-en model."""
    name = "vosk"

    def __init__(self):
        self._rec = None
        self.available = False
        self.error = None
        try:
            from vosk import Model, KaldiRecognizer  # noqa
        except Exception as e:
            self.error = f"vosk package not installed: {e}"
            return
        if not config.VOSK_MODEL_DIR.exists():
            self.error = f"vosk model dir missing: {config.VOSK_MODEL_DIR}"
            return
        try:
            from vosk import Model
            self._model = Model(str(config.VOSK_MODEL_DIR))
            self.available = True
        except Exception as e:
            self.error = f"failed to load vosk model: {e}"

    def transcribe(self, pcm_bytes: bytes) -> str:
        if not self.available:
            return ""
        from vosk import KaldiRecognizer
        import json
        rec = KaldiRecognizer(self._model, config.MIC_SAMPLE_RATE)
        rec.AcceptWaveform(pcm_bytes)
        result = json.loads(rec.FinalResult())
        return result.get("text", "").strip()


class GoogleSTT(_STTBackend):
    """Online. Uses SpeechRecognition's Google Web Speech endpoint."""
    name = "google"

    def __init__(self):
        self.available = False
        self.error = None
        try:
            import speech_recognition  # noqa
            self.available = True
        except Exception as e:
            self.error = f"SpeechRecognition not installed: {e}"

    def transcribe(self, pcm_bytes: bytes) -> str:
        if not self.available:
            return ""
        import speech_recognition as sr
        try:
            audio = sr.AudioData(pcm_bytes,
                                 sample_rate=config.MIC_SAMPLE_RATE,
                                 sample_width=2)   # int16 = 2 bytes
            return sr.Recognizer().recognize_google(audio)
        except sr.UnknownValueError:
            return ""                                  # speech simply wasn't understood
        except sr.RequestError as e:
            self.error = f"Google STT network/quota error: {e}"
            return ""
        except OSError as e:
            # e.g. the FLAC encoder is missing on ARM — `apt-get install flac`.
            self.error = f"audio conversion failed (is 'flac' installed?): {e}"
            return ""
        except Exception as e:
            self.error = f"Google STT failed: {e}"
            return ""


# ---------------------------------------------------------------------------
# TTS backends
# ---------------------------------------------------------------------------

class _TTSBackend:
    name = "abstract"
    available: bool = False
    error: str | None = None
    audio_mime: str = "audio/wav"
    def speak(self, text: str):
        """Block until playback completes; the chatbot calls this from a
        worker thread so the UI stays responsive."""
        raise NotImplementedError
    def synth_bytes(self, text: str) -> bytes:
        """Synthesise `text` and return audio bytes the browser can play.
        Default raises; subclasses override."""
        raise NotImplementedError(f"{self.name} backend does not expose synth_bytes")


class Pyttsx3TTS(_TTSBackend):
    """Offline. SAPI5 on Windows, eSpeak on Linux."""
    name = "pyttsx3"

    def __init__(self):
        self._lock = threading.Lock()
        self.available = False
        self.error = None
        self.rate = getattr(config, "PYTTSX3_RATE", 160)   # words/min; set live from Config
        try:
            import pyttsx3  # noqa
            self.available = True
        except Exception as e:
            self.error = f"pyttsx3 not installed: {e}"

    def _apply_rate(self, engine):
        try:
            engine.setProperty("rate", int(self.rate))
        except Exception:
            pass

    def speak(self, text: str):
        if not self.available or not text:
            return
        # pyttsx3's engine isn't thread-safe; create a fresh one per call
        # to avoid the "run loop already started" sharp edge.
        import pyttsx3
        with self._lock:
            try:
                engine = pyttsx3.init()
                self._apply_rate(engine)
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                self.error = f"pyttsx3 speak failed: {e}"

    def synth_bytes(self, text: str) -> bytes:
        """Render to a temp WAV file (SAPI5 / espeak both support save_to_file)."""
        if not self.available or not text:
            return b""
        import pyttsx3
        tmp = Path(tempfile.gettempdir()) / f"ev_tts_{os.getpid()}_{threading.get_ident()}.wav"
        with self._lock:
            try:
                engine = pyttsx3.init()
                self._apply_rate(engine)
                engine.save_to_file(text, str(tmp))
                engine.runAndWait()
                engine.stop()
                data = tmp.read_bytes() if tmp.exists() else b""
                return data
            except Exception as e:
                self.error = f"pyttsx3 synth_bytes failed: {e}"
                return b""
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass


class GTTSTTS(_TTSBackend):
    """Online. Saves MP3 to temp file, plays via pygame.mixer."""
    name = "gtts"
    audio_mime = "audio/mpeg"

    def __init__(self):
        self.available = False
        self.error = None
        self._mixer_ready = False
        # gTTS alone is enough for browser playback (synth_bytes).
        # pygame is only needed for server-side speak().
        try:
            import gtts  # noqa
            self.available = True
        except Exception as e:
            self.error = f"gtts not installed: {e}"
        try:
            import pygame  # noqa
            self._has_pygame = True
        except Exception:
            self._has_pygame = False

    def _ensure_mixer(self):
        if self._mixer_ready:
            return
        import pygame
        pygame.mixer.init()
        self._mixer_ready = True

    def speak(self, text: str):
        if not self.available or not text or not self._has_pygame:
            return
        from gtts import gTTS
        import pygame
        tmp = Path(tempfile.gettempdir()) / "ev_chatbot_gtts.mp3"
        try:
            tts = gTTS(text=text, lang="en")
            tts.save(str(tmp))
            self._ensure_mixer()
            pygame.mixer.music.load(str(tmp))
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(80)
            pygame.mixer.music.unload()
        except Exception as e:
            self.error = f"gtts speak failed: {e}"
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def synth_bytes(self, text: str) -> bytes:
        """Return MP3 bytes directly — no pygame needed for browser playback."""
        if not self.available or not text:
            return b""
        from gtts import gTTS
        import io
        try:
            tts = gTTS(text=text, lang="en")
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            return buf.getvalue()
        except Exception as e:
            self.error = f"gtts synth_bytes failed: {e}"
            return b""


# ---------------------------------------------------------------------------
# Registry — chatbot.py uses this to populate radio buttons
# ---------------------------------------------------------------------------

def build_stt_backends() -> dict[str, _STTBackend]:
    return {"vosk": VoskSTT(), "google": GoogleSTT()}

def build_tts_backends() -> dict[str, _TTSBackend]:
    return {"pyttsx3": Pyttsx3TTS(), "gtts": GTTSTTS()}
