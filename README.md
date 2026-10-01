# Maja — desktop prototype

Maja is Matty's personal assistant project. This first version runs on a computer and keeps a small, local memory of notes and tasks. It is a foundation for a future voice, camera, and physical robot version.

## Run it on Windows in VS Code

1. Install Python 3.11 or newer from python.org and select **Add Python to PATH** during installation.
2. Open this folder (`maja-starter`) in VS Code.
3. Open **Terminal → New Terminal**.
4. Run `python main.py` (or `py main.py` if Windows does not recognize `python`).

No packages, account, or API key are needed. The app creates `maja_data.json` next to `main.py` to store your notes and tasks on your own computer. Do not commit that personal file to GitHub.

## Try these messages

- `hello`
- `remember I want Maja to help me practice English`
- `what do you remember?`
- `add task sketch Maja's desk buddy shape`
- `show tasks`
- `finish task 1`
- `help`

## What this version does

The app recognizes a few commands and saves notes and tasks locally. It does **not** call an AI model yet, hear speech, see through a camera, or control hardware. That makes the first step easy to run and test while keeping the future hardware separate from the conversation interface.

## Next milestones

1. Add a model provider so Maja can hold open-ended conversations. Keep the API key in an environment variable, never in code or GitHub.
2. Add microphone input and spoken replies.
3. Add camera input with a visible on/off control.
4. Prototype the enclosure in CAD, then connect sensors and actuators through a hardware adapter.

The `Maja` class in `maja.py` is the core. The window in `main.py` can later be replaced by a mobile or robot interface without rewriting task storage.
