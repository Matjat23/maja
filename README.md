# Maja

[![Tests](https://github.com/Matjat23/maja/actions/workflows/tests.yml/badge.svg)](https://github.com/Matjat23/maja/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

Maja is my personal AI assistant. She runs completely on my own laptop, with no cloud and no API keys. I can type or talk to her, she keeps my notes and to-do list, reminds me when things are due, answers questions from my own notes and PDFs, and can look through the webcam when I turn it on.

The project is still in progress. The long-term goal is a small physical robot version of Maja that sits on my desk.

<!-- Add a screenshot or demo video here -->

## What she can do

- Chat using Llama 3.1, running locally through [Ollama](https://ollama.com)
- Save notes and tasks from normal conversation, and show what she saved
- Understand reminders like "Saturday at 6pm", "in 2 hours" or "by Friday", and show a Windows notification when they're due
- Listen through the microphone (faster-whisper) and answer out loud (Windows voices)
- Answer questions from my own documents and say which file and page the answer came from
- Look at what I hold up to the webcam (Qwen2.5-VL vision model)
- Chat from my phone over home Wi-Fi
- Start with Windows, so reminders work all day

## How it works

Every message goes through two calls to the local model. The first one only decides what to save (new notes, new tasks, finished tasks) and has to answer as JSON that matches a schema. The second one writes the actual reply, using my notes, my tasks, the last 20 messages and any matching parts of my documents. I split it up like this because Llama 3.1 was unreliable with tool calling: it saved almost every question as a note and sometimes refused to answer normal questions.

Dates are parsed in plain Python first ("in 2 hours", "tomorrow at 8"), with the model as a fallback, because the model got date math wrong too often.

For documents I use RAG. Files are split into chunks of about 1,000 characters, turned into embeddings with `nomic-embed-text` and stored in ChromaDB. For each question the closest chunks are given to the model. I measured the cut-off on test questions: questions about the documents had a cosine distance of 0.16 to 0.43 and normal chat 0.41 to 0.55, so chunks up to 0.45 are used and a source is only listed if it's a clear match (0.40) or the answer mentions it.

```
main.py (window)          server.py (phone)
        \                    /
         maja.py (commands, memory, prompts)
          |-- brain.py      Ollama: chat, JSON, embeddings, vision
          |-- library.py    documents: chunk, embed, search (ChromaDB)
          |-- reminders.py  due times and notifications
          |-- voice.py      speech to text and text to speech
          |-- camera.py     webcam
          |-- autostart.py  start with Windows
          `-- maja_data.json  notes, tasks and chat history (local only)
```

## Setup (Windows)

1. Install Python 3.11 or newer (check "Add Python to PATH").
2. Install [Ollama](https://ollama.com) and download the models:
   ```
   ollama pull llama3.1
   ollama pull nomic-embed-text
   ollama pull qwen2.5vl:3b
   ```
   The last two are only needed for documents and the camera.
3. Optional packages for the microphone, documents, phone and camera:
   ```
   py -m pip install -r requirements.txt
   ```
4. Start Maja:
   ```
   py main.py
   ```

Typing works without any extra packages. The first reply can take up to a minute while the model loads, after that it's a few seconds. Notes, tasks and chat history are saved in `maja_data.json`, which is not committed to Git.

`py main.py --demo` starts Maja with a separate empty memory and the sample files in `demo_documents/`. I use it for screenshots so my own data doesn't show.

## Using it

The buttons next to the text box:

- **Mic**: click, speak, click Stop. The first time it downloads the speech model (about 150 MB).
- **Voice on/off**: Maja reads her answers out loud.
- **Docs**: opens the documents folder. Put PDFs, .txt or .md files there and type `update documents`.
- **Camera**: turns the webcam on with a live preview. While it's on, the next message is about the picture, for example "What am I holding?". Photos are never saved.
- **Phone**: starts a small web server and shows a QR code. Scan it with a phone on the same Wi-Fi. The link contains a secret key and the server refuses requests without it. It's plain HTTP, so only use it on a network you trust.

Things to try:

- `I want to practice English every day`
- `Remind me to buy running shoes tomorrow at 5pm`
- `I bought the shoes!`
- `When is my exam?` (with the demo documents)
- `show tasks`, `what do you remember?`, `new chat`, `help`
- `start with windows` / `stop starting with windows`

Settings through environment variables: `MAJA_MODEL`, `MAJA_VISION_MODEL`, `MAJA_EMBED_MODEL`, `MAJA_WHISPER_MODEL` (for example `small` for better speech recognition) and `MAJA_CAMERA` (camera number).

## Tests

```
py -m unittest
```

The tests use fake models, so they don't need Ollama. The document, phone and camera tests are skipped if those packages aren't installed. GitHub Actions runs them on Python 3.11 and 3.13.

## Roadmap

- [x] Conversations with a local model, memory for notes and tasks
- [x] Voice and reminders
- [x] Camera, documents, phone access
- [ ] Robot version: design the case in CAD, then add sensors and motors
