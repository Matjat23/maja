"""Start Maja's desktop window with: python main.py"""

from pathlib import Path
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from brain import BrainError, OllamaBrain
from library import Library, LibraryError
from maja import Maja, describe_update, task_label
from reminders import describe_due, notify
from voice import VOICE_INPUT_AVAILABLE, VOICE_OUTPUT_AVAILABLE, Listener, Speaker, VoiceError


BG = "#1b1525"
PANEL = "#2a2038"
PINK = "#f4a4d4"
WHITE = "#f8f1fa"
RED = "#ff6b81"
GREY = "#6f6480"
SUBTITLE = "Your assistant, one step at a time"
REMINDER_CHECK_MS = 15_000
HERE = Path(__file__).parent
DOCUMENTS = HERE / "documents"


def main():
    try:
        brain = OllamaBrain()
        try:
            library = Library(DOCUMENTS, HERE / "library_db", brain)
        except LibraryError:
            library = None  # Maja explains how to install it when Matty asks about documents.
        maja = Maja(HERE / "maja_data.json", brain, library)
    except RuntimeError as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Maja could not start", str(error))
        root.destroy()
        return

    listener = Listener() if VOICE_INPUT_AVAILABLE else None
    speaker = Speaker()

    root = tk.Tk()
    root.title("Maja 💜")
    root.geometry("660x560")
    root.minsize(460, 380)
    root.configure(bg=BG)

    title = tk.Label(root, text="Maja  ✨", font=("Segoe UI", 24, "bold"), bg=BG, fg=PINK)
    title.pack(anchor="w", padx=22, pady=(18, 2))
    subtitle = tk.Label(root, text=SUBTITLE, font=("Segoe UI", 10), bg=BG, fg=WHITE)
    subtitle.pack(anchor="w", padx=24, pady=(0, 12))

    conversation = tk.Text(root, wrap="word", bg=PANEL, fg=WHITE, insertbackground=WHITE,
                           font=("Segoe UI", 11), relief="flat", padx=14, pady=14, state="disabled")
    conversation.pack(fill="both", expand=True, padx=20, pady=(0, 12))
    conversation.tag_configure("maja", foreground=PINK)

    row = tk.Frame(root, bg=BG)
    row.pack(fill="x", padx=20, pady=(0, 20))
    entry = tk.Entry(row, bg=PANEL, fg=WHITE, insertbackground=WHITE,
                     font=("Segoe UI", 11), relief="flat")
    entry.pack(side="left", fill="x", expand=True, ipady=11)

    def append(speaker_name, text, tag=None):
        conversation.configure(state="normal")
        conversation.insert("end", f"{speaker_name}: {text}\n\n", tag)
        conversation.configure(state="disabled")
        conversation.see("end")

    def maja_says(text):
        append("Maja", text, "maja")
        if maja.setting("speak", False):
            speaker.speak(text)

    # Slow work (the AI model, speech recognition) runs on background threads. They hand
    # results back through this queue, because only the main thread may touch the window.
    ui_calls = queue.Queue()

    def run_ui_calls():
        while True:
            try:
                ui_calls.get_nowait()()
            except queue.Empty:
                break
        root.after(100, run_ui_calls)

    def in_background(work):
        threading.Thread(target=work, daemon=True).start()

    # --- Typing and sending ---------------------------------------------------------
    busy = False

    def set_busy(value, status=SUBTITLE):
        nonlocal busy
        busy = value
        subtitle.configure(text=status)
        state = "disabled" if value else "normal"
        send_button.configure(state=state)
        mic_button.configure(state=state)

    def send(event=None):
        message = entry.get().strip()
        if not message or busy:
            return
        entry.delete(0, "end")
        append("You", message)
        speaker.stop()
        set_busy(True, "Maja is thinking…")

        def think():
            try:
                answer = maja.reply(message)
            except OSError as error:
                answer = f"I couldn't save that. Please check the folder permissions: {error}"
            ui_calls.put(lambda: (set_busy(False), maja_says(answer)))

        in_background(think)

    send_button = tk.Button(row, text="Send", command=send, bg=PINK, fg=BG,
                            font=("Segoe UI", 10, "bold"), relief="flat", padx=17)
    send_button.pack(side="left", padx=(8, 0), ipady=8)
    entry.bind("<Return>", send)

    # --- Voice: 🎤 to talk, 🔊 to hear Maja ------------------------------------------
    listening = False

    def toggle_listening():
        nonlocal listening
        if listener is None:
            maja_says("Talking to me needs a few extra packages. In a terminal, run: "
                      "py -m pip install -r requirements.txt  — then restart me.")
            return
        if not listening:
            try:
                listener.start()
            except VoiceError as error:
                maja_says(str(error))
                return
            listening = True
            speaker.stop()
            mic_button.configure(text="■", bg=RED)
            send_button.configure(state="disabled")
            subtitle.configure(text="Listening… click ■ when you're done")
            return
        listening = False
        mic_button.configure(text="🎤", bg=PANEL)
        set_busy(True, "Turning your voice into text…")

        def transcribe():
            try:
                text, problem = listener.stop(), None
            except VoiceError as error:
                text, problem = "", str(error)

            def show():
                set_busy(False)
                if problem:
                    maja_says(problem)
                elif not text:
                    subtitle.configure(text="I didn't catch that. Try again, a bit closer to the mic.")
                else:
                    entry.delete(0, "end")
                    entry.insert(0, text)
                    send()

            ui_calls.put(show)

        in_background(transcribe)

    def toggle_speaking():
        speak = not maja.setting("speak", False)
        maja.change_setting("speak", speak)
        speak_button.configure(text="🔊" if speak else "🔇", bg=PINK if speak else PANEL)
        if not speak:
            speaker.stop()

    mic_button = tk.Button(row, text="🎤", command=toggle_listening, bg=PANEL, fg=WHITE,
                           font=("Segoe UI Emoji", 12), relief="flat", padx=10)
    mic_button.pack(side="left", padx=(8, 0), ipady=3)
    speaking = maja.setting("speak", False)
    speak_button = tk.Button(row, text="🔊" if speaking else "🔇", command=toggle_speaking,
                             bg=PINK if speaking else PANEL, fg=WHITE,
                             font=("Segoe UI Emoji", 12), relief="flat", padx=10)
    if VOICE_OUTPUT_AVAILABLE:
        speak_button.pack(side="left", padx=(8, 0), ipady=3)

    # --- Documents: 📂 opens the folder; new files are read when Maja starts ---------------
    def open_documents():
        DOCUMENTS.mkdir(exist_ok=True)
        os.startfile(DOCUMENTS)
        maja_says("Put PDFs, .txt or .md files in this folder, then type 'update documents' "
                  "and ask me about them. 📚")

    if os.name == "nt":
        tk.Button(row, text="📂", command=open_documents, bg=PANEL, fg=WHITE,
                  font=("Segoe UI Emoji", 12), relief="flat", padx=10).pack(side="left", padx=(8, 0), ipady=3)

    # --- Reminders ---------------------------------------------------------------------
    def check_reminders():
        if not busy:  # Don't change tasks while Maja is in the middle of a reply.
            for task in maja.due_reminders():
                maja_says(f"⏰ Reminder: {task['text']} (due {describe_due(task['due'])})")
                in_background(lambda text=task_label(task): notify("Maja ⏰ Reminder", text))
        root.after(REMINDER_CHECK_MS, check_reminders)

    # --- Start ---------------------------------------------------------------------------
    for past in maja.history:
        if past["role"] == "user":
            append("You", past["content"])
        else:
            append("Maja", past["content"], "maja")
    if maja.history:
        append("Maja", "Welcome back, Matty! Type 'new chat' anytime for a fresh start. 💜", "maja")
    else:
        append("Maja", "Hi Matty! Type or press 🎤 to talk to me, or type 'help' to see what I can do. 💜", "maja")
    entry.focus_set()

    def warm_up():
        try:
            brain.warm_up()
        except BrainError:
            pass  # The real error message is shown when Matty sends a message.
        if listener is not None:
            try:
                listener.load()
            except VoiceError:
                pass  # Shown when Matty presses 🎤.
        if library is not None:
            try:
                report = library.update()
            except (BrainError, LibraryError):
                return  # Shown when Matty types 'update documents'.
            if any(report.values()):
                summary = describe_update(report)
                ui_calls.put(lambda: maja_says("I looked through your documents folder:\n" + summary))

    in_background(warm_up)
    root.after(100, run_ui_calls)
    root.after(1000, check_reminders)
    root.protocol("WM_DELETE_WINDOW", lambda: (speaker.stop(), root.destroy()))
    if "--minimized" in sys.argv:  # Started with Windows: wait quietly in the taskbar.
        root.iconify()
    root.mainloop()


if __name__ == "__main__":
    main()
