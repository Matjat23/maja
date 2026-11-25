# Maja

[![Tests](https://github.com/Matjat23/maja/actions/workflows/tests.yml/badge.svg)](https://github.com/Matjat23/maja/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)

Maja is my personal AI assistant. She runs completely on my own laptop, with no cloud and no API keys. I can type or talk to her, she keeps my notes and to-do list, reminds me when things are due, answers questions from my own notes and PDFs, and can look through the webcam when I turn it on.

The project is still in progress. The long-term goal is a small physical robot version of Maja that sits on my desk.

<!-- Add a screenshot or demo video here -->

## What she can do

- Chat using Llama 3.1, running locally through [Ollama](https://ollama.com). Answers appear word by word while the model writes them.
- Give me an overview of my day once every morning: what's due today, what's overdue and what's coming up this week
- Save notes and tasks from normal conversation, and show what she saved
- Remember every conversation, not just the last 20 messages, and bring up related older ones when they matter
- Understand reminders like "Saturday at 6pm", "in 2 hours" or "by Friday", and show a Windows notification when they're due
- Repeat reminders ("every day at 8", "every Monday", "every weekday", "every month") and run scheduled jobs ("job: every Monday at 8, plan my week")
- Listen through the microphone (faster-whisper) and answer out loud with a natural neural voice (Piper), sentence by sentence while the reply is still being written
- Wake up hands-free when I say "Hey Maja", for example "Hey Maja, what's my day?"
- Answer questions from my own documents and say which file and page the answer came from
- Look at what I hold up to the webcam (Qwen2.5-VL vision model)
- Chat from my phone over home Wi-Fi
- Start with Windows, so reminders work all day
- Read my calendars (Outlook, Google or TimeEdit) and include today's events in the morning overview
- Add events to my calendar and draft emails, but only after I say yes, and I still save or send them myself
- Hand hard questions to Claude when I ask for it, optionally with a web search, with a monthly spending limit
- Work on bigger tasks in the background ("job: make me a study plan for the exam") and save a report
- Decide herself what to look up in my documents, calendar and tasks before she answers ("research: when is my lab due and when can I work on it?")

## How it works

Every message goes through two calls to the local model. The first one only decides what to save (new notes, new tasks, finished tasks) and has to answer as JSON that matches a schema. The second one writes the actual reply, using my notes, my tasks, the last 20 messages and any matching parts of my documents. I split it up like this because Llama 3.1 was unreliable with tool calling: it saved almost every question as a note and sometimes refused to answer normal questions.

The daily overview works the same way: the list of tasks is built by code, so nothing gets left out or made up, and the model only writes the short greeting above it.

For "Hey Maja" there is no ready-made wake word model for her name, so I built it from the speech recognition Maja already has. A simple loudness check cuts the microphone sound into short pieces of speech (keeping the 0.4 seconds before each piece, otherwise the quiet start of "Hey" gets lost), and only those pieces are transcribed. If a piece starts with "Hey Maja" (or "Maya", "Maia", which is how Whisper often spells it), the rest is the message. Listening pauses while Maja is talking so she doesn't hear herself. I tested it by having other Piper voices say wake phrases and normal sentences into the same code: 34 of 36 were right and none of the normal sentences woke her.

Repeating reminders work the same way: "every day", "every Monday", "every weekday" and "every month" are recognized in Python. When a repeating reminder goes off it moves on to its next time instead of being marked done, and times that were missed while Maja was closed are skipped rather than repeated.

For long-term memory, every question and answer is saved in `chat_archive.jsonl` and as an embedding in its own ChromaDB collection. With each new message, the closest older conversations that aren't in the recent chat anymore are added to the model's instructions with their dates. I measured the cut-off on 8 stored conversations: questions about an old topic found the right one at a distance of 0.16 to 0.33, unrelated questions and small talk were 0.34 to 0.50, so the limit is 0.34. `forget chats` deletes all of it.

Questions that only ask for information ("explain...", "what is...") skip the save step completely. The model sometimes turned them into tasks or notes, and a simple rule in code is more reliable than more examples in the prompt.

Dates are parsed in plain Python first ("in 2 hours", "tomorrow at 8", "December 15 at 3pm", "15/12"), with the model as a fallback, because the model got date math wrong too often.

For documents I use RAG. Files are split into chunks of about 1,000 characters, turned into embeddings with `nomic-embed-text` and stored in ChromaDB. For each question the closest chunks are given to the model. I measured the cut-off on test questions: questions about the documents had a cosine distance of 0.16 to 0.43 and normal chat 0.41 to 0.55, so chunks up to 0.45 are used and a source is only listed if it's a clear match (0.40) or the answer mentions it.

```
main.py (window)          server.py (phone)
        \                    /
         maja.py (commands, memory, prompts)
          |-- brain.py      Ollama: chat, JSON, embeddings, vision
          |-- library.py    documents: chunk, embed, search (ChromaDB)
          |-- reminders.py  due times and notifications
          |-- agenda.py     calendar (ICS link, read-only)
          |-- cloud.py      Claude for think: and search:
          |-- jobs.py       background jobs: plan, research, write, review
          |-- agent.py      the loop where she picks what to look up (read-only tools)
          |-- memory.py     long-term memory of all conversations
          |-- actions.py    calendar events and email drafts (after a yes)
          |-- markdown.py   formatting in the chat window
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
3. Optional packages for the microphone, voice, documents, phone and camera:
   ```
   py -m pip install -r requirements.txt
   ```
   For the natural voice, download at least one Piper voice into `voices/` (about 60 MB each):
   ```
   py -m piper.download_voices --download-dir voices en_US-hfc_female-medium
   ```
   Without a Piper voice, Maja uses the Windows voice.
4. Start Maja:
   ```
   py main.py
   ```
5. Optional: `py autostart.py` puts Maja on the desktop and in the Start menu with her own icon, so she opens with a double click and without a console window.

Typing works without any extra packages. The first reply can take up to a minute while the model loads, after that it's a few seconds. Notes, tasks and chat history are saved in `maja_data.json`, which is not committed to Git.

`py main.py --demo` starts Maja with a separate empty memory and the sample files in `demo_documents/`. I use it for screenshots so my own data doesn't show.

## Phone outside home Wi-Fi (optional)

Phone access works on the home Wi-Fi out of the box. To use Maja from anywhere, I install [Tailscale](https://tailscale.com) on the laptop and the phone and sign in with the same account. Tailscale makes a private, encrypted network between my own devices, so Maja is never open to the public internet. When Maja finds a Tailscale address (100.64.0.0/10) she puts that in the QR code instead, and the same link works at home and away. The phone's keyboard dictation works for talking on the phone page.

## Face and robot body (first step)

The plan is that Maja's brain stays on the laptop and a small robot on my desk is her body. The first part of that body is her face: when phone access is on, Maja also shows a link to `/face`, a full-screen page for an old phone or tablet with eyes and a mouth that change with what she's doing (idle, listening, thinking, speaking). The page asks `GET /api/state` a few times a second, with the same secret key as the chat page. A future robot board (for example an ESP32 moving two servos) can use the same endpoint to nod or turn its head, which keeps all the hardware outside Maja's code.

## Calendar (optional)

Maja reads my calendars through their private ICS links. She can't change anything in them, and she doesn't need my password. Type `calendar` followed by a link to connect it, and do the same for every other calendar I want her to see. `calendar` shows the next 7 days from all of them, with each event's calendar name when there's more than one. `calendars` lists what's connected, `calendar remove 2` disconnects the second one and `calendar off` disconnects all of them.

Getting the link:

- Outlook: Settings, Calendar, Shared calendars, Publish a calendar. Pick the calendar and "Can view all details", click Publish and copy the ICS link.
- Google Calendar: Settings, click the calendar, Integrate calendar, copy "Secret address in iCal format". Each calendar has its own address.
- TimeEdit: open the schedule, click Subscribe and copy the link.

A link works like a password for reading the calendar, so links are only stored in `maja_data.json` and never sent to the language model. Repeating events are expanded into individual dates, times are converted to the computer's time zone, and a copy is kept so it also works offline. In demo mode a sample calendar is connected automatically.

## Jobs

A message that starts with `job:` (or `work on:`) is a bigger task that Maja works on in the background while I keep chatting. It goes through four roles, based on my separate agent team: a planner, a researcher (who gets what Maja looked up in my documents, calendar and tasks, see below, and web search when Claude is used), a writer and a reviewer. If the reviewer finds problems, the writer fixes them once and the reviewer checks again, so it can't loop forever. When it's done, Maja shows the start of the result, sends a notification and saves a report with every step in `jobs/`. `open last job` opens it.

A job that starts with a repeat phrase, like `job: every Monday at 8, plan my week`, is scheduled instead of run right away, and Maja starts it on her own at those times while she's running. `schedules` lists them and `delete schedule 1` removes one.

Jobs use Claude when an API key and budget are available, and the local model otherwise. A local job with the sample lecture notes (a 5-day study plan) took about 75 seconds.

## Looking things up herself

A message that starts with `research:` (or `look into:` / `find out:`) makes Maja work like an agent. The model gets the goal and four tools: search my documents, read the calendar for any dates, list my open tasks, and done. Each round it picks one tool, Python checks the choice and runs it, and the result goes back to the model so it can pick the next step. When it has enough, it answers from what it found and the reply ends with what it looked up. Jobs use the same loop after the planner, and the reviewer gets the same evidence, so it can catch dates or facts the evidence doesn't back up.

The tools only read. Nothing in the loop can change a file, the calendar or the task list; calendar events and emails still go through the preview and `yes` described below. Everything a tool returns is passed on as data, and the model is told never to follow instructions inside it. Because a small local model sometimes picks wrong, Python refuses unknown tools, empty searches and impossible dates, stops after two refused choices in a row, and ends the loop when a call is repeated, when a search gives the same results as before, or after 5 lookups.

I tested how llama3.1 chooses with 10 questions on test data. It picked the right first tool 10 out of 10 times, and needed 1.8 lookups on average (about 3 seconds per question). The first version kept looking things up until the limit; asking the model "is the goal covered?" before every choice and requiring every field in the JSON fixed that.

## Calendar events and email drafts

`add to calendar: dentist Friday at 3pm for 45 minutes` and `draft email: to anna@example.com, ask if we can move our meeting` work the same way: Maja works out the event or writes the email, shows it, and asks first. Only after `yes` does she open it in my own app, the event as an `.ics` file in Outlook or Windows Calendar and the email as a `mailto:` draft in my mail app, and I still click Save or Send myself. Maja never sends anything. Any other message cancels the waiting action. The time of an event is read in Python first, like reminders.

## Cloud brain (optional)

The local model is fine for everyday things but too small for hard questions. Messages that start with `think:` (or the brain button) go to Claude instead, and `search:` lets Claude search the web first. Everything else stays on the laptop. What gets sent is the question, the recent chat and Maja's instructions, which include my notes, open tasks and any matching document excerpts.

Setup: create an API key in the [Claude Console](https://console.anthropic.com/) and save it as a Windows environment variable called `ANTHROPIC_API_KEY`, then restart Maja. It uses Claude Opus 5.5 (`MAJA_CLOUD_MODEL` changes it). Every answer shows what it cost, and Maja stops using Claude when the monthly budget is reached: $5 by default, `cloud budget 10` changes it, `cloud budget` shows what's been spent.

## Using it

The buttons next to the text box:

- **Mic**: click, speak, click again to send. The first time it downloads the speech model (about 150 MB). When I ask by voice, Maja always answers out loud.
- **Speaker**: turns spoken replies on or off for typed messages too.
- **Ear**: turns "Hey Maja" on or off. While it's on, the status line says so, and the setting is remembered. The audio is only processed on the laptop and never saved.
- **Folder**: opens the documents folder. Put PDFs, .txt or .md files there and type `update documents`.
- **Camera**: turns the webcam on with a live preview. While it's on, the next message is about the picture, for example "What am I holding?". Photos are never saved.
- **Tasks**: shows or hides the task panel on the right, where open tasks and scheduled jobs can be ticked off or deleted with the mouse. It runs the same commands as typing them. When a calendar is connected, the top of the panel shows today's events from all calendars. Events that are over turn grey, and the list is read again every 30 minutes and after midnight.
- **Phone**: starts a small web server and shows a QR code. Scan it with a phone on the same Wi-Fi. The link contains a secret key and the server refuses requests without it. `new phone key` makes a new one, so old links stop working. It's plain HTTP, so only use it on a network you trust.

Things to try:

- `I want to practice English every day`
- `Remind me to buy running shoes tomorrow at 5pm`
- `I bought the shoes!`
- `When is my exam?` (with the demo documents)
- `briefing` (the daily overview, also shown automatically the first time Maja runs each day after 5 am)
- `show tasks`, `delete task 2`, `what do you remember?`, `new chat`, `help`
- `Remind me to take my vitamins every day at 8`, then later `I took my vitamins`
- `start with windows` / `stop starting with windows`
- `add to calendar: study group Thursday at 5pm` / `draft email: to my teacher, ask for feedback on my report`
- `voice` to list the installed voices, `voice amy` to switch
- `speaker` to list the sound outputs, `speaker realtek` to make her talk through the laptop speakers (`speaker default` follows Windows again)
- `think: help me plan my thesis chapters` / `search: opening hours of the Chalmers library`
- `job: make me a 5-day study plan for my exam using my lecture notes`, then `open last job`
- `research: what do I have this week and what should I prepare?`

Settings through environment variables: `MAJA_MODEL`, `MAJA_VISION_MODEL`, `MAJA_EMBED_MODEL`, `MAJA_WHISPER_MODEL` (for example `small` for better speech recognition), `MAJA_VOICE` (default voice) and `MAJA_CAMERA` (camera number).

## Tests

```
py -m unittest
```

The tests use fake models, so they don't need Ollama. The document, phone and camera tests are skipped if those packages aren't installed. GitHub Actions runs them on Python 3.11 and 3.13.

## Roadmap

- [x] Conversations with a local model, memory for notes and tasks
- [x] Voice and reminders
- [x] Camera, documents, phone access
- [x] Cloud brain, calendar, background jobs
- [x] Formatting, repeating reminders, scheduled jobs, long-term memory, calendar events and email drafts
- [x] Task panel, desktop app, phone through Tailscale, face page and state endpoint for a robot
- [x] Several calendars, today's events in the panel, an agent loop with read-only tools
- [ ] Robot version: a moving head (ESP32 + servos), then a printed case designed in CAD
