# Maja — a private, offline AI assistant

[![Tests](https://github.com/Matjat23/maja/actions/workflows/tests.yml/badge.svg)](https://github.com/Matjat23/maja/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Runs offline](https://img.shields.io/badge/AI-runs%20locally-purple)

Maja is a personal AI assistant that runs **entirely on your own computer**: no cloud, no API keys, no data leaving the machine. You can type or talk to her, she remembers what matters from the conversation, keeps your to-do list, and reminds you when things are due.

It's the first step of a bigger project: Maja will get a camera and, eventually, a small physical robot body ("desk buddy").

<!-- Add a screenshot or demo GIF here, e.g. ![Maja demo](docs/demo.gif) -->

## Highlights

- **Local LLM** — conversations run on Llama 3.1 through [Ollama](https://ollama.com), so everything stays private and works offline.
- **Smart memory** — a two-step design: first the model returns structured JSON (enforced with a schema) describing what to save, then it writes a normal reply. Questions and small talk save nothing; every saved item is shown as a receipt.
- **Voice in and out** — offline speech recognition with faster-whisper and spoken replies with the built-in Windows voices.
- **Reminders** — natural-language due times ("Saturday at 6pm", "in 2 hours", "by Friday"), backed by a plain-Python date parser because the model alone got date math wrong.
- **Tested** — 19 unit tests with a fake model, run on Python 3.11 and 3.13 by GitHub Actions on every push.
- **No heavy dependencies** — the core uses only the Python standard library (tkinter, urllib); voice packages are optional.

## How it works

```
 you ──▶ main.py (tkinter window, 🎤/🔊)
            │
            ▼
         maja.py  ── commands ("show tasks", "new chat", …) handled in code
            │
            ├─▶ 1. save step:  brain.py ─▶ Ollama (JSON schema, temperature 0)
            │                   → new notes / tasks / finished tasks
            ├─▶ 2. reply step: brain.py ─▶ Ollama (knows notes, tasks, last 20 messages)
            │
            ├── reminders.py  due-time parsing + Windows notifications
            ├── voice.py      faster-whisper (speech → text), Windows speech (text → speech)
            └── maja_data.json  notes, tasks and chat history, stored locally
```

| File | Role |
|---|---|
| `maja.py` | Core assistant: commands, memory, tasks, prompts |
| `brain.py` | Talks to the local model over Ollama's HTTP API |
| `voice.py` | Microphone input and spoken replies |
| `reminders.py` | Due-time parsing and notifications |
| `main.py` | Desktop window |
| `test_maja.py` | Unit tests (fake model, no Ollama needed) |

## Run it on Windows in VS Code

1. Install Python 3.11 or newer from python.org and select **Add Python to PATH** during installation.
2. Install [Ollama](https://ollama.com), then download Maja's AI models once: `ollama pull llama3.1` (about 4.9 GB) and `ollama pull nomic-embed-text` (about 270 MB, for documents).
3. Open this folder in VS Code.
4. Open **Terminal → New Terminal**.
5. Optional, for the microphone, documents and phone: `py -m pip install -r requirements.txt`
6. Run `python main.py` (or `py main.py` if Windows does not recognize `python`).

No account or API key is needed, and the AI runs entirely on your own computer. Typing works without any Python packages; only the 🎤 microphone, 📂 documents and 📱 phone need the packages in `requirements.txt`. The first reply after starting can take up to a minute while the model loads; after that, replies take a few seconds. To try a different Ollama model, set the `MAJA_MODEL` environment variable (for example `MAJA_MODEL=qwen2.5`).

The app creates `maja_data.json` next to `main.py` to store your notes, tasks, and recent conversation on your own computer. Do not commit that personal file to GitHub.

## Voice

- **🎤 Talk:** click 🎤, speak, then click ■. Speech recognition ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) runs offline on your computer; the first time it downloads its speech model (about 150 MB). Use `MAJA_WHISPER_MODEL=small` for better accuracy (slower).
- **🔊 Listen:** click 🔇/🔊 to turn spoken replies on or off. Maja uses the voices built into Windows.

If 🎤 says it can't use the microphone, open **Windows Settings → Privacy & security → Microphone** and allow desktop apps to use it.

## Reminders

Tell Maja when, and the task gets a due time: `Remind me to call mom Saturday at 6pm`, `in 2 hours`, `tomorrow at 8`, `by Friday` (only a day means 09:00). When it's due, Maja shows a Windows notification and a ⏰ message in the chat (and says it out loud if 🔊 is on). Reminders only appear **while Maja is open**; anything that came due while it was closed is shown the next time you start it.

To keep Maja running all day, type `start with windows`: Maja adds a shortcut to the Windows Startup folder (`autostart.py`) and opens minimized in the taskbar every time you log in, without a console window. Type `stop starting with windows` to undo it. (Ollama starts with Windows on its own.)

## Documents (ask Maja about your own notes)

Click 📂 to open Maja's `documents` folder and put PDFs, `.txt` or `.md` files in it (subfolders are fine). Maja reads new and changed files when she starts, or when you type `update documents`. Then just ask, for example `When is my HPC exam?`, and she answers from your files and lists them under the reply: `📚 Sources: hpc-lecture4.pdf p. 3`. If your files don't contain the answer, she says so.

This is RAG (retrieval-augmented generation), built in `library.py`:

1. **Extract:** text is read from each file (PDFs page by page, with [pypdf](https://pypi.org/project/pypdf/)).
2. **Chunk:** the text is split into pieces of about 1,000 characters, keeping paragraphs together, with a small overlap so no idea is cut in half.
3. **Embed:** each piece becomes a list of 768 numbers that captures its meaning, using the local `nomic-embed-text` model in Ollama.
4. **Store:** the pieces and their embeddings go into a [ChromaDB](https://www.trychroma.com/) vector database in `library_db/`.
5. **Retrieve:** a question is embedded the same way, and the closest pieces (cosine distance up to 0.45) are given to llama3.1 together with the question.
6. **Answer and cite:** the model answers from those pieces. A source is listed when it was a clear match (distance up to 0.40) or the answer names it. These cut-offs were measured: questions about the documents scored 0.16–0.43, ordinary chat 0.41–0.55.

Your documents and the database stay on your computer and are never committed to Git.

## Phone (📱)

Click 📱 to turn on phone access, then scan the QR code with your phone's camera. Your phone must be on the same Wi-Fi. A chat page opens that talks to the same Maja, with the same notes, tasks, reminders and documents. Chats from the phone also appear in the desktop window. Click 📱 again to turn it off; it's off every time Maja starts.

How it works (`server.py`): a small [FastAPI](https://fastapi.tiangolo.com/) web server runs inside Maja on port 8765. The QR link contains a secret key after the `#`, so it never appears in server logs; the phone remembers it and sends it with every request, and anything without the right key is refused. A lock makes sure the phone, the window and the reminders never change Maja's data at the same time.

The first time, Windows may ask whether Python may use the network: allow it on **private** networks. The connection is plain HTTP, so only use phone access on a network you trust, like your home Wi-Fi.

## Try these messages

- `I want to practice English every day` (Maja saves it as a note)
- `Remind me to buy running shoes tomorrow at 5pm` (Maja adds a task with a reminder)
- `I bought the shoes!` (Maja marks the task as done)
- `hello`
- `remember I want Maja to help me practice English`
- `what do you remember?`
- `add task sketch Maja's desk buddy shape`
- `show tasks`
- `finish task 1`
- `help`
- `Can you help me plan my study week?` (any normal question goes to the AI)
- `new chat` (forget the conversation, keep notes and tasks)
- `show documents` / `update documents` (see or refresh the files Maja has read)

## Run the tests

```
python -m unittest -v
```

The tests use a fake AI model and a fake embedding model, so they don't need Ollama. The document tests need `chromadb` and `pypdf` and are skipped without them. GitHub runs them automatically on every push and pull request.

## What this version does

The app recognizes a few commands and saves notes and tasks locally. Anything that isn't a command is answered by a local AI model (llama3.1 through Ollama), which knows your saved notes and tasks. Before each reply, a separate step asks the model for a small structured answer about what to save (new notes, new tasks, finished tasks), so Maja can update your lists while you chat without saving questions or small talk. Every action is shown as a receipt under Maja's reply (📝, ✅, 🎉), so you always see what was saved. The last 20 messages are kept between restarts. You can talk to Maja and hear her answer, tasks with a time get reminders, and she can answer questions from your own documents with sources. It does **not** see through a camera or control hardware yet.

## Next milestones

1. ~~Add a model provider so Maja can hold open-ended conversations.~~ Done (local llama3.1 via Ollama, `brain.py`), including saving notes and tasks from normal conversation.
2. ~~Add microphone input and spoken replies.~~ Done (`voice.py`), plus reminders (`reminders.py`).
3. Add camera input with a visible on/off control.
4. Prototype the enclosure in CAD, then connect sensors and actuators through a hardware adapter.

The `Maja` class in `maja.py` is the core, `brain.py` connects it to the AI model, `voice.py` handles the microphone and speaking, `reminders.py` handles due times and notifications, `library.py` reads and searches your documents, and `server.py` serves the phone page. The window in `main.py` can later be replaced by a mobile or robot interface without rewriting task storage.
