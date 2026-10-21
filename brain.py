"""Connects Maja to a local AI model running in Ollama (https://ollama.com).

Nothing leaves your computer: Ollama runs the model locally, and this module talks
to it over http://localhost. Change the model with the MAJA_MODEL environment variable.
"""

import contextlib
import json
import os
import urllib.error
import urllib.request


class BrainError(Exception):
    """A friendly, user-facing explanation of why the model could not answer."""


class OllamaBrain:
    def __init__(self, model=None, host=None, timeout=300):
        self.model = model or os.getenv("MAJA_MODEL", "llama3.1")
        # Turns text into numbers (embeddings) so Maja can search Matty's documents.
        self.embed_model = os.getenv("MAJA_EMBED_MODEL", "nomic-embed-text")
        # Looks at photos from the camera.
        self.vision_model = os.getenv("MAJA_VISION_MODEL", "qwen2.5vl:3b")
        # 127.0.0.1, not "localhost": on Windows, "localhost" first tries IPv6 and
        # wastes about 2 seconds on every request.
        self.host = (host or os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        # The first reply can take a minute while the model loads into memory.
        self.timeout = timeout
        # How much text the model can read at once (its "working memory"), in tokens.
        # Ollama's default of about 4,000 silently dropped the older part of long chats.
        # 8,000 still fits on an 8 GB graphics card; 16,000 spilled over to the processor.
        # It must be the same in every request, or Ollama reloads the model each time.
        self.context = int(os.getenv("MAJA_CONTEXT", "8192"))

    def _options(self, **extra):
        return {"num_ctx": self.context, **extra}

    def _post(self, path, payload):
        with self._errors(payload):
            with self._open(path, payload) as response:
                return json.loads(response.read().decode("utf-8"))

    def _open(self, path, payload):
        request = urllib.request.Request(
            self.host + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        return urllib.request.urlopen(request, timeout=self.timeout)

    @contextlib.contextmanager
    def _errors(self, payload):
        """Turn network and Ollama errors into BrainError with a message Matty can act on."""
        try:
            yield
        except urllib.error.HTTPError as error:
            if error.code == 404:
                model = payload.get("model", self.model)
                raise BrainError(
                    f"The model '{model}' isn't downloaded yet. "
                    f"Run 'ollama pull {model}' in a terminal, then try again."
                ) from error
            raise BrainError(f"Ollama returned an error ({error.code}).") from error
        except (urllib.error.URLError, ConnectionError) as error:
            raise BrainError(
                "I can't reach Ollama. Open the Ollama app (or run 'ollama serve'), "
                "then try again."
            ) from error
        except TimeoutError as error:
            raise BrainError("The model took too long to answer. Please try again.") from error

    def _chat(self, messages, options=None, **extra):
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": self._options(**(options or {})),
            **extra,
        })
        return (data.get("message") or {}).get("content", "").strip()

    def warm_up(self, model=None):
        """Load a model into memory so the first real reply is fast."""
        payload = {"model": model or self.model, "keep_alive": "30m"}
        if payload["model"] == self.model:
            payload["options"] = self._options()  # Load it with the right working memory.
        self._post("/api/generate", payload)

    def chat(self, messages):
        """Send a list of {'role', 'content'} messages and return the reply text."""
        reply = self._chat(messages)
        if not reply:
            raise BrainError("The model sent an empty reply. Please try again.")
        return reply

    def chat_stream(self, messages):
        """Like chat(), but yields the reply in small pieces while the model writes it."""
        payload = {"model": self.model, "messages": messages, "stream": True, "keep_alive": "30m",
                   "options": self._options()}
        with self._errors(payload):
            with self._open("/api/chat", payload) as response:
                # Ollama sends one JSON object per line until "done" is true.
                for line in response:
                    if not line.strip():
                        continue
                    part = json.loads(line)
                    piece = (part.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
                    if part.get("done"):
                        break

    def see(self, messages, photo):
        """Like chat(), but the last message comes with a photo (base64 JPEG).

        Uses a separate vision model; the first photo can take a while as it loads.
        """
        *earlier, last = messages
        data = self._post("/api/chat", {
            "model": self.vision_model,
            "messages": [*earlier, {**last, "images": [photo]}],
            "stream": False,
            "keep_alive": "10m",
        })
        reply = (data.get("message") or {}).get("content", "").strip()
        if not reply:
            raise BrainError("I couldn't make sense of the photo. Please try again.")
        return reply

    def embed(self, texts):
        """Turn each text into a list of numbers; similar meanings get similar numbers."""
        data = self._post("/api/embed", {"model": self.embed_model, "input": texts, "keep_alive": "30m"})
        return data["embeddings"]

    def extract(self, messages, schema):
        """Ask for an answer shaped like the JSON `schema` and return it as a dict."""
        reply = self._chat(messages, format=schema, options={"temperature": 0})
        try:
            return json.loads(reply)
        except json.JSONDecodeError as error:
            raise BrainError("The model sent an answer I couldn't read.") from error
