"""Start Maja's first desktop window with: python main.py"""

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox

from brain import BrainError, OllamaBrain
from maja import Maja


BG = "#1b1525"
PANEL = "#2a2038"
PINK = "#f4a4d4"
WHITE = "#f8f1fa"


def main():
    try:
        brain = OllamaBrain()
        maja = Maja(Path(__file__).with_name("maja_data.json"), brain)
    except RuntimeError as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Maja could not start", str(error))
        root.destroy()
        return

    root = tk.Tk()
    root.title("Maja 💜")
    root.geometry("620x520")
    root.minsize(420, 360)
    root.configure(bg=BG)

    title = tk.Label(root, text="Maja  ✨", font=("Segoe UI", 24, "bold"), bg=BG, fg=PINK)
    title.pack(anchor="w", padx=22, pady=(18, 2))
    subtitle = tk.Label(root, text="Your assistant, one step at a time", font=("Segoe UI", 10), bg=BG, fg=WHITE)
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

    def append(speaker, text, tag=None):
        conversation.configure(state="normal")
        conversation.insert("end", f"{speaker}: {text}\n\n", tag)
        conversation.configure(state="disabled")
        conversation.see("end")

    # The AI model can take a few seconds, so replies are made on a background
    # thread and handed back to the window through this queue.
    replies = queue.Queue()
    busy = False

    def think(message):
        try:
            replies.put(maja.reply(message))
        except OSError as error:
            replies.put(f"I couldn't save that. Please check the folder permissions: {error}")

    def check_replies():
        nonlocal busy
        try:
            answer = replies.get_nowait()
        except queue.Empty:
            root.after(100, check_replies)
            return
        append("Maja", answer, "maja")
        busy = False
        subtitle.configure(text="Your assistant, one step at a time")
        send_button.configure(state="normal")

    def send(event=None):
        nonlocal busy
        message = entry.get().strip()
        if not message or busy:
            return
        busy = True
        entry.delete(0, "end")
        append("You", message)
        subtitle.configure(text="Maja is thinking…")
        send_button.configure(state="disabled")
        threading.Thread(target=think, args=(message,), daemon=True).start()
        root.after(100, check_replies)

    def warm_up():
        try:
            brain.warm_up()
        except BrainError:
            pass  # The real error message is shown when Matty sends a message.

    send_button = tk.Button(row, text="Send", command=send, bg=PINK, fg=BG,
                            font=("Segoe UI", 10, "bold"), relief="flat", padx=17)
    send_button.pack(side="left", padx=(8, 0), ipady=8)
    entry.bind("<Return>", send)
    append("Maja", "Hi Matty! Ask me anything, or type 'help' to see my commands. 💜", "maja")
    entry.focus_set()
    threading.Thread(target=warm_up, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
