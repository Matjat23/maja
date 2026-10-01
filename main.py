"""Start Maja's first desktop window with: python main.py"""

from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from maja import Maja


BG = "#1b1525"
PANEL = "#2a2038"
PINK = "#f4a4d4"
WHITE = "#f8f1fa"


def main():
    try:
        maja = Maja(Path(__file__).with_name("maja_data.json"))
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

    def send(event=None):
        message = entry.get().strip()
        if not message:
            return
        entry.delete(0, "end")
        append("You", message)
        try:
            append("Maja", maja.reply(message), "maja")
        except OSError as error:
            append("Maja", f"I couldn't save that. Please check the folder permissions: {error}", "maja")

    send_button = tk.Button(row, text="Send", command=send, bg=PINK, fg=BG,
                            font=("Segoe UI", 10, "bold"), relief="flat", padx=17)
    send_button.pack(side="left", padx=(8, 0), ipady=8)
    entry.bind("<Return>", send)
    append("Maja", "Hi Matty! Type 'help' to get started. 💜", "maja")
    entry.focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
