"""Start Maja's desktop window with: python main.py"""

from pathlib import Path
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from brain import BrainError, OllamaBrain
from camera import CAMERA_AVAILABLE, Camera, CameraError, photo_for_model, preview_png
from library import Library, LibraryError
from maja import Maja, describe_update, task_label
from reminders import describe_due, notify
from server import SERVER_AVAILABLE, PhoneServer, new_key
from voice import VOICE_INPUT_AVAILABLE, VOICE_OUTPUT_AVAILABLE, Listener, Speaker, VoiceError

try:
    import qrcode
except ImportError:
    qrcode = None


BG = "#1b1525"
PANEL = "#2a2038"
PINK = "#f4a4d4"
WHITE = "#f8f1fa"
RED = "#ff6b81"
GREY = "#6f6480"
SUBTITLE = "Your assistant, one step at a time"
BUTTON_FONT = ("Segoe UI", 10, "bold")
REMINDER_CHECK_MS = 15_000
HERE = Path(__file__).parent
# Demo mode (py main.py --demo) uses a separate folder with its own notes, tasks, chat and
# sample documents, so recordings never show Matty's real data.
DEMO = "--demo" in sys.argv
DATA = HERE / "demo" if DEMO else HERE
DOCUMENTS = DATA / "documents"


def prepare_demo():
    """Copy the sample documents into the demo folder the first time."""
    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    if not any(DOCUMENTS.iterdir()):
        for sample in (HERE / "demo_documents").iterdir():
            shutil.copy(sample, DOCUMENTS / sample.name)


def main():
    try:
        brain = OllamaBrain()
        if DEMO:
            prepare_demo()
        try:
            library = Library(DOCUMENTS, DATA / "library_db", brain)
        except LibraryError:
            library = None  # Maja explains how to install it when Matty asks about documents.
        maja = Maja(DATA / "maja_data.json", brain, library)
    except RuntimeError as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Maja could not start", str(error))
        root.destroy()
        return

    listener = Listener() if VOICE_INPUT_AVAILABLE else None
    camera = Camera() if CAMERA_AVAILABLE else None
    speaker = Speaker()
    # Only one thing may change Maja's data at a time: the window, the phone, or reminders.
    maja_lock = threading.Lock()

    root = tk.Tk()
    root.title("Maja 💜")
    root.geometry("720x620")
    root.minsize(560, 420)
    root.configure(bg=BG)

    title = tk.Label(root, text="Maja  ✨", font=("Segoe UI", 24, "bold"), bg=BG, fg=PINK)
    title.pack(anchor="w", padx=22, pady=(18, 2))
    subtitle = tk.Label(root, text=SUBTITLE, font=("Segoe UI", 10), bg=BG, fg=WHITE)
    subtitle.pack(anchor="w", padx=24, pady=(0, 12))

    # The input row is packed before the conversation so it never gets pushed out of a
    # small window; the conversation takes whatever space is left.
    row = tk.Frame(root, bg=BG)
    row.pack(side="bottom", fill="x", padx=20, pady=(0, 20))

    conversation = tk.Text(root, wrap="word", bg=PANEL, fg=WHITE, insertbackground=WHITE,
                           font=("Segoe UI", 11), relief="flat", padx=14, pady=14, state="disabled")
    conversation.pack(fill="both", expand=True, padx=20, pady=(0, 12))
    conversation.tag_configure("maja", foreground=PINK)
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
        frame = camera.frame() if camera is not None else None
        if busy or not (message or frame is not None):
            return
        entry.delete(0, "end")
        # With the camera on, the message is about what the camera sees right now.
        photo = photo_for_model(frame) if frame is not None else None
        append("You (photo)" if photo else "You", message or "What do you see?")
        speaker.stop()
        set_busy(True, "Maja is looking..." if photo else "Maja is thinking...")

        def think():
            try:
                with maja_lock:
                    answer = maja.reply_about_photo(message, photo) if photo else maja.reply(message)
            except OSError as error:
                answer = f"I couldn't save that. Please check the folder permissions: {error}"
            ui_calls.put(lambda: (set_busy(False), maja_says(answer)))

        in_background(think)

    send_button = tk.Button(row, text="Send", command=send, bg=PINK, fg=BG,
                            font=("Segoe UI", 10, "bold"), relief="flat", padx=17)
    send_button.pack(side="left", padx=(8, 0), ipady=8)
    entry.bind("<Return>", send)

    # --- Voice: Mic to talk, Voice on/off to hear Maja ------------------------------------------
    listening = False

    def toggle_listening():
        nonlocal listening
        if listener is None:
            maja_says("Talking to me needs a few extra packages. In a terminal, run: "
                      "py -m pip install -r requirements.txt, then restart me.")
            return
        if not listening:
            try:
                listener.start()
            except VoiceError as error:
                maja_says(str(error))
                return
            listening = True
            speaker.stop()
            mic_button.configure(text="Stop", bg=RED)
            send_button.configure(state="disabled")
            subtitle.configure(text="Listening... click Stop when you're done")
            return
        listening = False
        mic_button.configure(text="Mic", bg=PANEL)
        set_busy(True, "Turning your voice into text...")

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
        speak_button.configure(text="Voice on" if speak else "Voice off", bg=PINK if speak else PANEL)
        if not speak:
            speaker.stop()

    mic_button = tk.Button(row, text="Mic", command=toggle_listening, bg=PANEL, fg=WHITE,
                           font=BUTTON_FONT, relief="flat", padx=12)
    mic_button.pack(side="left", padx=(8, 0), ipady=8)
    speaking = maja.setting("speak", False)
    speak_button = tk.Button(row, text="Voice on" if speaking else "Voice off", command=toggle_speaking,
                             bg=PINK if speaking else PANEL, fg=WHITE,
                             font=BUTTON_FONT, relief="flat", padx=12)
    if VOICE_OUTPUT_AVAILABLE:
        speak_button.pack(side="left", padx=(8, 0), ipady=8)

    # --- Documents: Docs opens the folder; new files are read when Maja starts ---------------
    def open_documents():
        DOCUMENTS.mkdir(exist_ok=True)
        os.startfile(DOCUMENTS)
        maja_says("Put PDFs, .txt or .md files in this folder, then type 'update documents' "
                  "and ask me about them.")

    if os.name == "nt":
        tk.Button(row, text="Docs", command=open_documents, bg=PANEL, fg=WHITE,
                  font=BUTTON_FONT, relief="flat", padx=12).pack(side="left", padx=(8, 0), ipady=8)

    # --- Camera: the Camera button turns it on with a live preview; sending then includes a photo ---------
    camera_frame = tk.Frame(root, bg=BG)
    camera_label = tk.Label(camera_frame, text="Camera on: your next message includes a photo",
                            font=("Segoe UI", 9, "bold"), bg=BG, fg=RED)
    camera_label.pack(anchor="w")
    camera_preview = tk.Label(camera_frame, bg=BG)
    camera_preview.pack(anchor="w", pady=(4, 0))
    preview_image = None

    def update_preview():
        nonlocal preview_image
        if camera is None or not camera.is_on:
            return
        frame = camera.frame()
        if frame is not None:
            preview_image = tk.PhotoImage(data=preview_png(frame))
            camera_preview.configure(image=preview_image)
        root.after(80, update_preview)

    def toggle_camera():
        if camera is None:
            maja_says("The camera needs an extra package. In a terminal, run: "
                      "py -m pip install -r requirements.txt, then restart me.")
            return
        if camera.is_on:
            camera.turn_off()
            camera_frame.pack_forget()
            camera_button.configure(bg=PANEL)
            subtitle.configure(text=SUBTITLE)
            return
        try:
            camera.turn_on()
        except CameraError as error:
            maja_says(str(error))
            return
        camera_frame.pack(side="bottom", before=conversation, anchor="w", padx=20, pady=(0, 10))
        camera_button.configure(bg=RED)
        subtitle.configure(text="Camera on. Ask me something, like 'what am I holding?'")
        update_preview()

        def load_vision_model():
            # The graphics card can't hold both models, so the vision model is loaded
            # now, while Matty lines up the photo, instead of after pressing Send.
            try:
                brain.warm_up(brain.vision_model)
            except BrainError:
                pass  # Shown when Matty sends the photo.

        in_background(load_vision_model)

    camera_button = tk.Button(row, text="Camera", command=toggle_camera, bg=PANEL, fg=WHITE,
                              font=BUTTON_FONT, relief="flat", padx=12)
    camera_button.pack(side="left", padx=(8, 0), ipady=8)

    # --- Phone: the Phone button turns on a small web server and shows a QR code to scan ------------------
    phone = None

    def show_phone_message(message, reply):
        ui_calls.put(lambda: (append("You (phone)", message), append("Maja", reply, "maja")))

    def show_qr(link):
        window = tk.Toplevel(root, bg=BG)
        window.title("Maja on your phone")
        tk.Label(window, text="Scan with your phone's camera", font=("Segoe UI", 13, "bold"),
                 bg=BG, fg=PINK).pack(padx=20, pady=(16, 8))
        code = qrcode.QRCode(border=2)
        code.add_data(link)
        matrix = code.get_matrix()
        cell = max(3, 260 // len(matrix))
        canvas = tk.Canvas(window, width=len(matrix) * cell, height=len(matrix) * cell,
                           bg="white", highlightthickness=0)
        for y, line in enumerate(matrix):
            for x, dark in enumerate(line):
                if dark:
                    canvas.create_rectangle(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell,
                                            fill="black", width=0)
        canvas.pack(padx=20)
        tk.Label(window, text="Your phone must be on the same Wi-Fi.\nIf Windows asks about the "
                 "firewall, allow Python on private networks.\nThe link contains your secret key, "
                 "so don't share it.", font=("Segoe UI", 9), bg=BG, fg=WHITE,
                 justify="center").pack(padx=20, pady=12)

    def toggle_phone():
        nonlocal phone
        if not SERVER_AVAILABLE or qrcode is None:
            maja_says("Phone access needs a few extra packages. In a terminal, run: "
                      "py -m pip install -r requirements.txt, then restart me.")
            return
        if phone is not None:
            phone.stop()
            phone = None
            phone_button.configure(bg=PANEL)
            maja_says("Phone access is off.")
            return
        key = maja.setting("phone_key")
        if not key:
            key = new_key()
            maja.change_setting("phone_key", key)
        phone = PhoneServer(maja, maja_lock, key, show_phone_message)
        try:
            phone.start()
        except OSError as error:
            phone = None
            maja_says(f"I couldn't start phone access: {error}")
            return
        phone_button.configure(bg=PINK)
        maja_says("Phone access is on. Scan the QR code with your phone (same Wi-Fi). "
                  "Click Phone again to turn it off.")
        show_qr(phone.link())

    phone_button = tk.Button(row, text="Phone", command=toggle_phone, bg=PANEL, fg=WHITE,
                             font=BUTTON_FONT, relief="flat", padx=12)
    phone_button.pack(side="left", padx=(8, 0), ipady=8)

    # --- Reminders ---------------------------------------------------------------------
    def check_reminders():
        # Skip this round if Maja is busy replying (here or to the phone); try again soon.
        if maja_lock.acquire(blocking=False):
            try:
                due = maja.due_reminders()
            finally:
                maja_lock.release()
            for task in due:
                maja_says(f"Reminder: {task['text']} (due {describe_due(task['due'])})")
                in_background(lambda text=task_label(task): notify("Maja reminder", text))
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
        append("Maja", "Hi Matty! Type, or press Mic to talk to me. Type 'help' to see what I can do. 💜", "maja")
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
                pass  # Shown when Matty presses Mic.

    def read_documents():
        # Runs alongside warm_up(), so documents are ready without waiting for the models.
        try:
            report = library.update()
        except (BrainError, LibraryError):
            return  # Shown when Matty types 'update documents'.
        if any(report.values()):
            summary = describe_update(report)
            ui_calls.put(lambda: maja_says("I looked through your documents folder:\n" + summary))

    in_background(warm_up)
    if library is not None:
        in_background(read_documents)
    root.after(100, run_ui_calls)
    root.after(1000, check_reminders)
    def close():
        speaker.stop()
        if camera is not None:
            camera.turn_off()
        if phone is not None:
            phone.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    if "--minimized" in sys.argv:  # Started with Windows: wait quietly in the taskbar.
        root.iconify()
    root.mainloop()


if __name__ == "__main__":
    main()
