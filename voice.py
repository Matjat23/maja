"""Talking to Maja: microphone + offline speech recognition, and spoken replies.

Speech recognition uses faster-whisper and runs on your own computer. The first time,
it downloads the speech model (about 150 MB). Spoken replies use the voices built into
Windows. Voice is optional: without the packages in requirements.txt, Maja still works
by typing.
"""

import os
import re
import subprocess
import sys
import threading

try:
    import numpy as np
    import sounddevice as sd
    from faster_whisper import WhisperModel
    VOICE_INPUT_AVAILABLE = True
except ImportError:
    VOICE_INPUT_AVAILABLE = False

VOICE_OUTPUT_AVAILABLE = sys.platform == "win32"
SAMPLE_RATE = 16000
# Emoji would be read out loud ("purple heart"), so they are removed before speaking.
EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]")


class VoiceError(Exception):
    """A friendly, user-facing explanation of why voice didn't work."""


class Listener:
    """Records from the microphone between start() and stop(), then turns speech into text."""

    def __init__(self, model_size=None):
        self.model_size = model_size or os.getenv("MAJA_WHISPER_MODEL", "base")
        self._model = None
        self._loading = threading.Lock()
        self._stream = None
        self._chunks = []

    def load(self):
        """Load the speech model (slow the first time). Safe to call more than once."""
        with self._loading:
            if self._model is not None:
                return
            try:
                self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            except Exception as error:  # Download or load problems come in many types.
                raise VoiceError(
                    "I couldn't load the speech model. The first time, I need internet "
                    "to download it (about 150 MB)."
                ) from error

    def start(self):
        self._chunks = []
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                          callback=lambda data, *_: self._chunks.append(data.copy()))
            self._stream.start()
        except Exception as error:
            self._stream = None
            raise VoiceError(
                "I can't use the microphone. Check Windows Settings → Privacy & security → "
                "Microphone, and allow desktop apps to use it."
            ) from error

    def stop(self):
        """Stop recording and return what was said ('' if nothing was heard)."""
        if self._stream is None:
            return ""
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._chunks:
            return ""
        audio = np.concatenate(self._chunks)[:, 0]
        if len(audio) < SAMPLE_RATE // 2:  # Less than half a second.
            return ""
        self.load()
        segments, _ = self._model.transcribe(audio, beam_size=1, vad_filter=True)
        return "".join(segment.text for segment in segments).strip()


# Reads text from standard input and speaks it, preferring a female voice for Maja.
_SPEAK_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $voice.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female) } catch {}
$voice.Speak([Console]::In.ReadToEnd())
"""


class Speaker:
    """Speaks text out loud with a Windows voice. A new sentence interrupts the old one."""

    def __init__(self):
        self._process = None

    def speak(self, text):
        self.stop()
        text = EMOJI.sub("", text)
        if not VOICE_OUTPUT_AVAILABLE or not text.strip():
            return
        script = "[Console]::InputEncoding = [Text.Encoding]::UTF8\n" + _SPEAK_SCRIPT
        try:
            self._process = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._process.stdin.write(text.encode("utf-8"))
            self._process.stdin.close()
        except OSError:
            self._process = None

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.kill()
        self._process = None
