"""Talking to Maja: microphone + offline speech recognition, spoken replies, and "Hey Maja".

Speech recognition uses faster-whisper and runs on your own computer. The first time,
it downloads the speech model (about 150 MB). Spoken replies use a Piper neural voice
from the voices/ folder when one is installed, and the built-in Windows voice otherwise.
Voice is optional: without the packages in requirements.txt, Maja still works by typing.
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import numpy as np
    import sounddevice as sd
    from faster_whisper import WhisperModel
    VOICE_INPUT_AVAILABLE = True
except ImportError:
    VOICE_INPUT_AVAILABLE = False

try:
    from piper import PiperVoice
    NEURAL_VOICE_AVAILABLE = VOICE_INPUT_AVAILABLE  # Playback needs sounddevice and numpy.
except ImportError:
    NEURAL_VOICE_AVAILABLE = False

VOICE_OUTPUT_AVAILABLE = NEURAL_VOICE_AVAILABLE or sys.platform == "win32"
SAMPLE_RATE = 16000
VOICES = Path(__file__).with_name("voices")
DEFAULT_VOICE = "en_US-hfc_female-medium"

# Emoji would be read out loud ("purple heart"), so they are removed before speaking.
EMOJI = re.compile("[\U0001F000-\U0001FFFF☀-➿️]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class VoiceError(Exception):
    """A friendly, user-facing explanation of why voice didn't work."""


def text_for_speech(text):
    """What Maja should actually say: no emoji, no markdown, no list of sources."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("📚")]
    text = EMOJI.sub("", "\n".join(lines))
    return re.sub(r"[*#`_]+", "", text)


def split_sentences(text):
    return [part.strip() for part in SENTENCE_END.split(text) if part.strip()]


class Listener:
    """Records from the microphone between start() and stop(), then turns speech into text."""

    def __init__(self, model_size=None):
        self.model_size = model_size or os.getenv("MAJA_WHISPER_MODEL", "base")
        self._model = None
        self._loading = threading.Lock()
        self._using = threading.Lock()
        self._stream = None
        self._chunks = []

    @property
    def recording(self):
        return self._stream is not None

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

    def transcribe(self, audio, hint=None):
        """Turn 16 kHz mono audio (a numpy array) into text."""
        self.load()
        with self._using:  # The window and "Hey Maja" share one model.
            segments, _ = self._model.transcribe(audio, beam_size=1, vad_filter=True,
                                                 initial_prompt=hint)
            return "".join(segment.text for segment in segments).strip()

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
        return self.transcribe(audio)


# --- Speaking -------------------------------------------------------------------------

# Reads text from standard input and speaks it, preferring a female voice for Maja.
_SPEAK_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $voice.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female) } catch {}
$voice.Speak([Console]::In.ReadToEnd())
"""


def installed_voices():
    return sorted(path.stem for path in VOICES.glob("*.onnx")) if VOICES.exists() else []


class Speaker:
    """Speaks text out loud. With a Piper voice, sentences are spoken one by one as soon
    as each is ready, so Maja can start talking while the model is still writing.
    Calling stop() (or speak() with new text) interrupts her right away."""

    def __init__(self, voice=None):
        self.voice = voice or os.getenv("MAJA_VOICE", DEFAULT_VOICE)
        self._loaded = {}
        self._loading = threading.Lock()
        self._sentences = queue.Queue()
        self._round = 0  # Increased by stop(); sentences from an older round are dropped.
        self._pending = ""
        self._busy = False
        self._process = None
        self._worker = None

    @property
    def neural(self):
        return NEURAL_VOICE_AVAILABLE and (VOICES / f"{self.voice}.onnx").exists()

    @property
    def speaking(self):
        if self._process is not None and self._process.poll() is None:
            return True
        return self._busy or not self._sentences.empty()

    def load(self):
        """Load the neural voice now, so the first sentence isn't delayed (takes a few seconds)."""
        with self._loading:
            if self.neural and self.voice not in self._loaded:
                self._loaded[self.voice] = PiperVoice.load(str(VOICES / f"{self.voice}.onnx"))
            return self._loaded.get(self.voice)

    def speak(self, text):
        """Interrupt whatever Maja is saying and say this instead."""
        self.stop()
        self.say(text)

    def say(self, text):
        """Say this after anything that's already queued."""
        text = text_for_speech(text)
        if not text.strip() or not VOICE_OUTPUT_AVAILABLE:
            return
        if not self.neural:
            self._windows_speak(text)
            return
        for sentence in split_sentences(text):
            self._sentences.put((self._round, sentence))
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._play_sentences, daemon=True)
            self._worker.start()

    def feed(self, piece):
        """Take part of a reply that's still being written; complete sentences are spoken now."""
        self._pending += piece
        if not self.neural:
            return  # The Windows voice can only read whole texts; see flush().
        parts = SENTENCE_END.split(self._pending)
        if len(parts) > 1:
            self._pending = parts[-1]
            self.say(" ".join(parts[:-1]))

    def flush(self, rest=""):
        """Say whatever is left of a streamed reply, plus any extra text (like receipts)."""
        text = f"{self._pending}\n{rest}".strip()
        self._pending = ""
        self.say(text)

    def stop(self):
        self._round += 1
        self._pending = ""
        while not self._sentences.empty():
            try:
                self._sentences.get_nowait()
            except queue.Empty:
                break
        if NEURAL_VOICE_AVAILABLE:
            sd.stop()
        if self._process and self._process.poll() is None:
            self._process.kill()
        self._process = None

    def _play_sentences(self):
        while True:
            try:
                round_, sentence = self._sentences.get(timeout=2)
            except queue.Empty:
                return
            if round_ != self._round:
                continue
            self._busy = True
            try:
                voice = self.load()
                chunks = list(voice.synthesize(sentence))
                if round_ == self._round and chunks:
                    audio = np.concatenate([chunk.audio_float_array for chunk in chunks])
                    sd.play(audio, chunks[0].sample_rate)
                    sd.wait()  # Returns early when stop() calls sd.stop().
            except Exception:
                pass  # A broken sentence shouldn't stop the rest of the reply.
            finally:
                self._busy = False

    def _windows_speak(self, text):
        if sys.platform != "win32":
            return
        if self._process and self._process.poll() is None:
            self._process.kill()
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


# --- "Hey Maja" -----------------------------------------------------------------------

# Whisper often spells the name Maya, Maia or Mia, so all of those count.
WAKE_PHRASE = re.compile(
    r"^\W*(?:(?:hey|hi|hello|ok|okay)\W+)?(?:maja|maya|maia|mia|majah)\b[\s,.!?]*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_wake(text):
    """('Hey Maja, what's my day?') -> (True, "what's my day?"); no wake phrase -> (False, '')."""
    match = WAKE_PHRASE.match(text.strip())
    if not match:
        return False, ""
    return True, match.group(1).strip().rstrip(".")


class WakeListener:
    """Listens for "Hey Maja" while it's turned on.

    A simple loudness check cuts the microphone sound into short pieces of speech, and only
    those pieces are turned into text. Nothing is saved. When a piece starts with the wake
    phrase, the rest of it is the message ("Hey Maja, what's my day?"). If Matty only says
    "Hey Maja", the next thing they say within a few seconds is the message.
    """

    BLOCK = 480                # 30 ms of audio.
    END_SILENCE = 0.8          # Seconds of quiet that end a piece of speech.
    MIN_SPEECH = 0.35          # Shorter sounds (a cough, a click) are ignored.
    MAX_SPEECH = 10.0
    FOLLOW_UP = 7.0            # How long Maja waits for the message after "Hey Maja".

    def __init__(self, listener, on_wake, on_message, paused=lambda: False):
        self.listener = listener
        self.on_wake = on_wake
        self.on_message = on_message
        self.paused = paused
        self._blocks = queue.Queue()
        self._stream = None
        self._awake_until = 0.0

    @property
    def running(self):
        return self._stream is not None

    def start(self):
        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                          blocksize=self.BLOCK,
                                          callback=lambda data, *_: self._blocks.put(data[:, 0].copy()))
            self._stream.start()
        except Exception as error:
            self._stream = None
            raise VoiceError(
                "I can't use the microphone. Check Windows Settings → Privacy & security → "
                "Microphone, and allow desktop apps to use it."
            ) from error
        threading.Thread(target=self._listen, daemon=True).start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._blocks.put(None)

    def _listen(self):
        for audio in self.pieces_of_speech(self._blocks):
            self.handle(audio)

    def pieces_of_speech(self, blocks):
        """Group 30 ms blocks (from a queue, ending with None) into pieces of speech."""
        noise = 0.002
        piece, quiet, voiced = [], 0.0, 0.0
        block_seconds = self.BLOCK / SAMPLE_RATE
        # The start of a word is quiet, so the last 0.4 s before speech is kept and added
        # to the front of the piece. Without it, "Hey Maja" at the start gets cut off.
        before = []
        before_blocks = int(0.4 / block_seconds)
        while True:
            block = blocks.get()
            if block is None:
                return
            if self.paused():
                piece, quiet, voiced, before = [], 0.0, 0.0, []
                continue
            loudness = float(np.sqrt(np.mean(block ** 2)))
            speech = loudness > max(0.004, noise * 3)
            if not speech and not piece:
                noise = 0.95 * noise + 0.05 * loudness  # Follow the room's background noise.
                before = (before + [block])[-before_blocks:]
                continue
            if not piece:
                piece, before = before, []
            piece.append(block)
            if speech:
                voiced += block_seconds
                quiet = 0.0
            else:
                quiet += block_seconds
            length = len(piece) * block_seconds
            if quiet >= self.END_SILENCE or length >= self.MAX_SPEECH:
                if voiced >= self.MIN_SPEECH:
                    yield np.concatenate(piece)
                piece, quiet, voiced = [], 0.0, 0.0

    def handle(self, audio):
        text = self.listener.transcribe(audio, hint="Hey Maja.")
        if not text:
            return
        if time.monotonic() < self._awake_until:
            self._awake_until = 0.0
            self.on_message(text)
            return
        woke, message = parse_wake(text)
        if not woke:
            return
        if len(message.split()) >= 2:
            self.on_message(message)
        else:
            self._awake_until = time.monotonic() + self.FOLLOW_UP
            self.on_wake()
