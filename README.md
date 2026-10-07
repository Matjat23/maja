# Maja — desktop prototype

[![Tests](https://github.com/Matjat23/maja/actions/workflows/tests.yml/badge.svg)](https://github.com/Matjat23/maja/actions/workflows/tests.yml)

Maja is Matty's personal assistant project. This first version runs on a computer and keeps a small, local memory of notes and tasks. It is a foundation for a future voice, camera, and physical robot version.

## Run it on Windows in VS Code

1. Install Python 3.11 or newer from python.org and select **Add Python to PATH** during installation.
2. Install [Ollama](https://ollama.com), then download Maja's AI model once: `ollama pull llama3` (about 4.7 GB).
3. Open this folder in VS Code.
4. Open **Terminal → New Terminal**.
5. Run `python main.py` (or `py main.py` if Windows does not recognize `python`).

No Python packages, account, or API key are needed, and the AI runs entirely on your own computer. The first reply after starting can take up to a minute while the model loads; after that, replies take a few seconds. To try a different Ollama model, set the `MAJA_MODEL` environment variable (for example `MAJA_MODEL=llama3.1`).

The app creates `maja_data.json` next to `main.py` to store your notes and tasks on your own computer. Do not commit that personal file to GitHub.

## Try these messages

- `hello`
- `remember I want Maja to help me practice English`
- `what do you remember?`
- `add task sketch Maja's desk buddy shape`
- `show tasks`
- `finish task 1`
- `help`
- `Can you help me plan my study week?` (any normal question goes to the AI)
- `new chat` (forget the conversation, keep notes and tasks)

## Run the tests

```
python -m unittest -v
```

The tests use a fake AI model, so they don't need Ollama. GitHub runs them automatically on every push and pull request.

## What this version does

The app recognizes a few commands and saves notes and tasks locally. Anything that isn't a command is answered by a local AI model (llama3 through Ollama), which also knows your saved notes and open tasks. It does **not** hear speech, see through a camera, or control hardware yet.

## Next milestones

1. ~~Add a model provider so Maja can hold open-ended conversations.~~ Done (local llama3 via Ollama, `brain.py`).
2. Add microphone input and spoken replies.
3. Add camera input with a visible on/off control.
4. Prototype the enclosure in CAD, then connect sensors and actuators through a hardware adapter.

The `Maja` class in `maja.py` is the core, and `brain.py` connects it to the AI model. The window in `main.py` can later be replaced by a mobile or robot interface without rewriting task storage.
