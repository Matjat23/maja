"""Connects Maja to a local AI model running in Ollama (https://ollama.com).

Nothing leaves your computer: Ollama runs the model locally, and this module talks
to it over http://localhost. Change the model with the MAJA_MODEL environment variable.
"""

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
        # 127.0.0.1, not "localhost": on Windows, "localhost" first tries IPv6 and
        # wastes about 2 seconds on every request.
        self.host = (host or os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        # The first reply can take a minute while the model loads into memory.
        self.timeout = timeout

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.host + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
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

    def _chat(self, messages, **extra):
        data = self._post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            **extra,
        })
        return (data.get("message") or {}).get("content", "").strip()

    def warm_up(self):
        """Load the model into memory so the first real reply is fast."""
        self._post("/api/generate", {"model": self.model, "keep_alive": "30m"})

    def chat(self, messages):
        """Send a list of {'role', 'content'} messages and return the reply text."""
        reply = self._chat(messages)
        if not reply:
            raise BrainError("The model sent an empty reply. Please try again.")
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
