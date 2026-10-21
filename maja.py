"""Local command and memory layer for the Maja prototype."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import autostart
from brain import BrainError
from agenda import AgendaError, describe_event
from library import CLEAR_MATCH, LibraryError, source_label
from reminders import DUE_FORMAT, describe_due, guess_due, parse_due


# How many recent chat messages Maja keeps in mind (and saves) between conversations.
HISTORY_LIMIT = 20

# The shape of the answer when Maja asks the model what to save from a message.
MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "array", "items": {"type": "string"}},
        "new_tasks": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "due": {"type": "string"}},
            "required": ["text", "due"],
        }},
        "finished_task_numbers": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["notes", "new_tasks", "finished_task_numbers"],
}


def memory_rules(now):
    """Instructions for the save step. They include real dates, so the model can
    turn 'Saturday at 6pm' into an exact time without doing calendar math."""
    days = "\n".join(
        f"- {label}{(now + timedelta(days=i)):%A %Y-%m-%d}"
        for i, label in enumerate(["today: ", "tomorrow: "] + [""] * 6)
    )
    friday = next(now + timedelta(days=i) for i in range(1, 8) if (now + timedelta(days=i)).weekday() == 4)
    monday = next(now + timedelta(days=i) for i in range(1, 8) if (now + timedelta(days=i)).weekday() == 0)
    tomorrow = now + timedelta(days=1)
    return f"""You update Matty's personal notes and to-do list. Read ONLY Matty's latest message and decide what to store.

Rules:
- notes: only lasting personal facts, goals, or preferences that Matty states about themself, or things Matty explicitly asks to remember. Write them as short sentences about Matty.
- new_tasks: only things Matty asks to be reminded of or says they need to do. "text" starts with a verb and leaves out the date and time.
- "due": when Matty says when (a day and/or a time), write it as "YYYY-MM-DD HH:MM" using the calendar below. Only a day: use 09:00. Only a time: today, or tomorrow if that time has passed. "In 2 hours" etc.: add it to the current time. No day or time mentioned: "".
- finished_task_numbers: numbers of open tasks from the list that Matty says they completed.
- Questions, greetings, small talk and requests for information or advice store NOTHING.
- Don't repeat notes that are already saved.
- When unsure, store nothing. Most messages store nothing.

Now: {now:%A %Y-%m-%d %H:%M}
Calendar:
{days}

Examples:
"How are you?" -> {{"notes": [], "new_tasks": [], "finished_task_numbers": []}}
"What should I eat before an exam?" -> {{"notes": [], "new_tasks": [], "finished_task_numbers": []}}
"Translate 'thank you' to French" -> {{"notes": [], "new_tasks": [], "finished_task_numbers": []}}
"I love hiking on weekends" -> {{"notes": ["Matty loves hiking on weekends"], "new_tasks": [], "finished_task_numbers": []}}
"I need to clean my room" -> {{"notes": [], "new_tasks": [{{"text": "Clean my room", "due": ""}}], "finished_task_numbers": []}}
"I have to hand in my essay by Monday" -> {{"notes": [], "new_tasks": [{{"text": "Hand in my essay", "due": "{monday:%Y-%m-%d} 09:00"}}], "finished_task_numbers": []}}
"Remind me to email my teacher on Friday" -> {{"notes": [], "new_tasks": [{{"text": "Email my teacher", "due": "{friday:%Y-%m-%d} 09:00"}}], "finished_task_numbers": []}}
"Remind me tomorrow at 7pm to call grandma" -> {{"notes": [], "new_tasks": [{{"text": "Call grandma", "due": "{tomorrow:%Y-%m-%d} 19:00"}}], "finished_task_numbers": []}}
With open task "2. Pay rent": "I paid the rent" -> {{"notes": [], "new_tasks": [], "finished_task_numbers": [2]}}
"""


def task_label(task):
    """'Call mom', or with a due date 'Call mom (alarm clock icon, Sat 10 Oct, 18:00)'."""
    if task.get("due"):
        return f"{task['text']} (⏰ {describe_due(task['due'])})"
    return task["text"]


# Messages starting like this go to Claude. Short words need a colon ("think: ..."), so an
# ordinary sentence like "Think about it" never costs money by accident.
CLOUD_PREFIX = re.compile(
    r"^\s*(?:(think harder|search the web for)\b[\s,:.-]*|(think|search)\s*:\s*)(.+)$",
    re.IGNORECASE | re.DOTALL,
)


def cloud_mode(message):
    """'think: why is the sky blue?' -> ('cloud', 'why is the sky blue?').
    Plain messages -> (None, message), so they stay on this computer."""
    match = CLOUD_PREFIX.match(message)
    if not match:
        return None, message
    word = (match.group(1) or match.group(2)).lower()
    return ("web" if word.startswith("search") else "cloud"), match.group(3).strip()


def describe_update(report):
    """A short, friendly summary of Library.update()."""
    lines = [f"📄 Read: {name}" for name in report["added"]]
    lines += [f"🔄 Re-read (changed): {name}" for name in report["updated"]]
    lines += [f"🗑️ Forgot (deleted): {name}" for name in report["removed"]]
    lines += [f"⚠️ {problem}" for problem in report["problems"]]
    return "\n".join(lines) or "My documents are up to date. 📚"


class Maja:
    def __init__(self, data_file: Path, brain=None, library=None, cloud=None, agenda=None):
        self.data_file = Path(data_file)
        self.data = self._load()
        self.brain = brain
        # Matty's documents (see library.py). None when that feature isn't installed.
        self.library = library
        # Claude, for "think:" and "search:" questions (see cloud.py). Created by the window.
        self.cloud = cloud
        # Matty's calendar, read-only (see agenda.py).
        self.agenda = agenda
        # The conversation is stored with the notes, so it survives a restart.
        self.history = self.data["history"]

    def _load(self):
        if not self.data_file.exists():
            return {"notes": [], "tasks": [], "history": []}
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
            data.setdefault("history", [])
            if not all(isinstance(data.get(key), list) for key in ("notes", "tasks", "history")):
                raise ValueError("Invalid Maja data")
            return data
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            raise RuntimeError(
                f"Could not read {self.data_file}. Your data was not changed."
            ) from exc

    def _save(self):
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.data_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.data_file)

    def setting(self, name, default=None):
        return self.data.get("settings", {}).get(name, default)

    def change_setting(self, name, value):
        self.data.setdefault("settings", {})[name] = value
        self._save()

    def _add_note(self, note):
        if note.lower().rstrip(".") in (n.lower().rstrip(".") for n in self.data["notes"]):
            return False
        self.data["notes"].append(note)
        self._save()
        return True

    def _add_task(self, text, due=None):
        task = {"text": text, "done": False}
        if due:
            task["due"] = due.strftime(DUE_FORMAT)
        self.data["tasks"].append(task)
        self._save()
        return task

    def due_reminders(self, now=None):
        """Return open tasks that are due and haven't been reminded about yet.

        Each one is marked as reminded, so it only pops up once.
        """
        now = now or datetime.now()
        due = [task for task in self.data["tasks"]
               if task.get("due") and not task["done"] and not task.get("reminded")
               and datetime.strptime(task["due"], DUE_FORMAT) <= now]
        for task in due:
            task["reminded"] = True
        if due:
            self._save()
        return due

    def _finish_task(self, number):
        """Mark task `number` (1-based) as done and return it, or None if it doesn't exist."""
        if not 1 <= number <= len(self.data["tasks"]):
            return None
        task = self.data["tasks"][number - 1]
        task["done"] = True
        self._save()
        return task

    def reply(self, message: str, on_text=None) -> str:
        """Answer a message and return the full reply.

        If on_text is given, it's called with each new piece of the AI's answer while the
        model is still writing, so the window can show the words as they arrive. Commands
        are answered at once and don't call it.
        """
        message = message.strip()
        lower = message.lower()
        if not message:
            return "Type a message and I'll be here. 💜"
        if lower in {"hello", "hi", "hey"}:
            return "Hi Matty! I'm Maja. What would you like to work on today? 💜"
        if lower in {"help", "commands"}:
            return ("Just talk to me normally, for example 'remind me to call mom' or "
                    "'I want to run 5 km this spring', and I'll save what matters. "
                    "Commands: 'remember ...', 'what do you remember?', 'add task ...', "
                    "'show tasks', 'finish task 1', 'what time is it?', 'new chat', "
                    "'show documents', 'update documents', 'briefing', 'calendar', 'start with windows', "
                    "'think: ...' (a harder question for Claude), 'search: ...' (Claude searches "
                    "the web), 'job: ...' (a bigger task I work on in the background) or "
                    "'cloud budget'. Put PDFs and notes in my "
                    "documents folder and ask me about them.")
        if lower in {"new chat", "reset chat"}:
            self.history.clear()
            self._save()
            return "Fresh start! Your notes and tasks are still saved. 💜"
        if lower.startswith("remember "):
            note = message[9:].strip()
            if not note:
                return "Tell me what you would like me to remember."
            self._add_note(note)
            return "I'll remember that. 💜"
        if lower in {"what do you remember?", "show notes", "my notes"}:
            notes = self.data["notes"]
            return "Your notes:\n" + "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1)) if notes else "I don't have any notes yet."
        if lower.startswith("add task "):
            task = message[9:].strip()
            if not task:
                return "Tell me the task you want to add."
            self._add_task(task)
            return f"Added task: {task}"
        if lower in {"show tasks", "my tasks"}:
            tasks = self.data["tasks"]
            return ("Your tasks:\n" + "\n".join(
                f"{i}. {'✓' if task['done'] else '○'} {task_label(task)}"
                for i, task in enumerate(tasks, 1))) if tasks else "Your task list is empty."
        if lower.startswith("finish task "):
            try:
                task = self._finish_task(int(lower[len("finish task "):].strip()))
            except ValueError:
                task = None
            if task is None:
                return "Use a task number from 'show tasks', for example 'finish task 1'."
            return f"Done! {task['text']} 🎉"
        if lower in {"show documents", "my documents", "update documents", "read my documents"}:
            if self.library is None:
                return ("I can't read documents yet. In a terminal, run: "
                        "py -m pip install -r requirements.txt, then restart me.")
            if lower in {"show documents", "my documents"}:
                files = self.library.documents()
                if not files:
                    return f"I haven't read any documents yet. Put PDFs, .txt or .md files in {self.library.folder}"
                return "Documents I've read:\n" + "\n".join(f"📄 {name}" for name in files)
            try:
                return describe_update(self.library.update())
            except (BrainError, LibraryError) as error:
                return str(error)
        if lower in {"start with windows", "start automatically"}:
            if autostart.enable():
                return ("Done! I'll start (minimized) every time you log in to Windows, so I "
                        "can remind you even if you forget to open me. 🚀 To undo it, type "
                        "'stop starting with windows'.")
            return "Sorry, I couldn't add myself to Windows startup."
        if lower in {"stop starting with windows", "don't start with windows"}:
            autostart.disable()
            return "Okay, I won't start with Windows anymore. Open me whenever you like. 💜"
        if lower in {"what time is it?", "time"}:
            return f"Your computer's local time is {datetime.now():%H:%M}."
        if lower in {"briefing", "morning briefing", "my day", "what's my day?", "what's my day"}:
            return self.morning_briefing()
        command, _, argument = message.partition(" ")
        if command.lower() == "calendar" or lower in {"my calendar", "what's on my calendar?",
                                                      "what's on my calendar"}:
            return self._calendar_command("" if command.lower() != "calendar" else argument.strip())
        if lower.startswith("cloud budget"):
            return self._cloud_budget(lower[len("cloud budget"):].strip(" $"))
        if self.brain is None:
            return ("I can't answer open-ended questions yet. Type 'help' to see what "
                    "this first version can do. We'll add an AI conversation engine next.")
        mode, question = cloud_mode(message)
        if mode and self.cloud is None:
            return "Thinking harder isn't set up on this computer yet (see 'Cloud brain' in the README)."
        return self._ask_brain(question, on_text, mode)

    def _calendar_command(self, argument):
        if self.agenda is None:
            return ("Reading a calendar needs extra packages. In a terminal, run: "
                    "py -m pip install -r requirements.txt, then restart me.")
        try:
            if argument.lower() == "off":
                self.agenda.disconnect()
                return "Okay, I've disconnected your calendar."
            if argument:
                self.agenda.connect(argument)
                return "Your calendar is connected! 📅 Here's what's coming up:\n" + self._week_ahead()
            return self._week_ahead()
        except AgendaError as error:
            return str(error)

    def _week_ahead(self, now=None):
        now = now or datetime.now()
        events = self.agenda.events(now, now + timedelta(days=7))
        if not events:
            return "Nothing on your calendar for the next 7 days."
        return "\n".join(f"📅 {describe_event(event, with_day=True)}" for event in events)

    def _calendar_for_prompt(self):
        """The next 7 days of the calendar, for Maja's instructions ('' if there's none)."""
        if self.agenda is None or not self.agenda.url:
            return ""
        try:
            now = datetime.now()
            events = self.agenda.events(now, now + timedelta(days=7))[:25]
        except AgendaError:
            return ""
        lines = "\n".join(f"- {describe_event(event, with_day=True)}" for event in events) or "(nothing)"
        return f"\nMatty's calendar for the next 7 days:\n{lines}"

    def _cloud_budget(self, amount):
        if self.cloud is None:
            return "Thinking harder isn't set up on this computer yet (see 'Cloud brain' in the README)."
        if amount:
            try:
                budget = float(amount)
                if budget < 0:
                    raise ValueError
            except ValueError:
                return "Give the budget in dollars, for example 'cloud budget 10'."
            self.change_setting("cloud_budget", budget)
        return (f"Cloud budget: ${self.cloud.budget:.2f} per month. Spent this month: "
                f"${self.cloud.spent_this_month():.2f}.")

    def briefing_due(self, now=None):
        """True once per day, from 5 in the morning, until the briefing has been shown."""
        now = now or datetime.now()
        return now.hour >= 5 and self.setting("last_briefing") != f"{now:%Y-%m-%d}"

    def morning_briefing(self, now=None):
        """Today's overview: a short greeting from the model, then the facts from the task
        list. The facts are written by code, so nothing gets left out or made up."""
        now = now or datetime.now()
        self.change_setting("last_briefing", f"{now:%Y-%m-%d}")
        today = now.date()
        open_tasks = [task for task in self.data["tasks"] if not task["done"]]

        def due(task):
            return datetime.strptime(task["due"], DUE_FORMAT)

        timed = sorted((task for task in open_tasks if task.get("due")), key=due)
        due_today = [task for task in timed if due(task).date() == today]
        overdue = [task for task in timed if due(task).date() < today]
        this_week = [task for task in timed if today < due(task).date() <= today + timedelta(days=7)]
        undated = [task for task in open_tasks if not task.get("due")]

        lines = [f"Today, {now:%A %d %B}:"]
        if self.agenda is not None and self.agenda.url:
            try:
                lines += [f"📅 {describe_event(event)}" for event in self.agenda.day(today)]
            except AgendaError:
                lines.append("📅 (I couldn't read your calendar right now.)")
        lines += [f"⏰ {due(task):%H:%M} {task['text']}" for task in due_today]
        if len(lines) == 1:
            lines.append("Nothing is due today.")
        if overdue:
            lines.append("Overdue:")
            lines += [f"⚠️ {task['text']} (was due {describe_due(task['due'])})" for task in overdue]
        if this_week:
            lines.append("Coming up this week:")
            lines += [f"📅 {due(task):%a %d %b}: {task['text']}" for task in this_week]
        if undated:
            lines.append(f"📝 {len(undated)} more open task{'s' if len(undated) != 1 else ''} "
                         "without a date (type 'show tasks').")
        facts = "\n".join(lines)
        return f"{self._briefing_greeting(now, facts)}\n\n{facts}"

    def _briefing_greeting(self, now, facts):
        part_of_day = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
        fallback = f"Good {part_of_day}, Matty! 💜"
        if self.brain is None:
            return fallback
        notes = "\n".join(f"- {note}" for note in self.data["notes"]) or "(none)"
        try:
            greeting = self.brain.chat([
                {"role": "system", "content": (
                    "You are Maja, Matty's warm personal assistant. Write a greeting of one or "
                    f"two short sentences for Matty's {part_of_day} overview. It starts with "
                    f"'Good {part_of_day}, Matty'. Encourage them for what's on the list below, "
                    "and if it fits, mention one of their goals. Don't list the tasks again (they "
                    "are shown below your greeting) and don't invent anything: you don't know "
                    "how Matty's day has gone. Example: 'Good morning, Matty! Two things today "
                    "and your essay is due Thursday, so a calm start will help.'")},
                {"role": "user", "content": f"Today's overview:\n{facts}\n\nMatty's notes and goals:\n{notes}"},
            ]).strip()
        except BrainError:
            return fallback
        # Small models sometimes stop early or wander off; then the plain greeting is better.
        if not greeting.startswith(f"Good {part_of_day}") or len(greeting) < 30 or len(greeting) > 300:
            return fallback
        return greeting

    def reply_about_photo(self, message, photo):
        """Answer a message about a camera photo (base64 JPEG). The photo isn't stored;
        the chat history only records that there was one."""
        if self.brain is None:
            return "I need my AI model to look at photos."
        question = message.strip() or "What do you see?"
        system = (
            "You are Maja, Matty's warm, encouraging personal assistant. Matty sent a photo "
            "from their webcam together with a message. Answer the message about the photo "
            "in a few friendly sentences. Describe only what you can actually see, and say "
            "so if something is unclear."
        )
        try:
            answer = self.brain.see([{"role": "system", "content": system},
                                     {"role": "user", "content": question}], photo)
        except BrainError as error:
            return str(error)
        self.history += [{"role": "user", "content": f"(with a photo 📷) {question}"},
                         {"role": "assistant", "content": answer}]
        del self.history[:-HISTORY_LIMIT]
        self._save()
        return answer + "\n📷 Looked at one photo (not saved)"

    def _notes_and_tasks(self):
        notes = "\n".join(f"- {note}" for note in self.data["notes"]) or "(none yet)"
        tasks = "\n".join(
            f"{i}. {task_label(task)}" for i, task in enumerate(self.data["tasks"], 1) if not task["done"]
        ) or "(none)"
        return f"Saved notes about Matty:\n{notes}\nMatty's open tasks:\n{tasks}"

    def _remember(self, message):
        """Step 1: ask the model what (if anything) to save from Matty's message.

        Returns a receipt line (icon + 'Saved note: ...' etc.) for everything that was saved.
        """
        context = ""
        if self.history and self.history[-1]["role"] == "assistant":
            context = f"Maja's previous message (context only, store nothing from it):\n{self.history[-1]['content']}\n\n"
        changes = self.brain.extract([
            {"role": "system", "content": memory_rules(datetime.now()) + "\n" + self._notes_and_tasks()},
            {"role": "user", "content": f"{context}Matty's latest message:\n{message}"},
        ], MEMORY_SCHEMA)

        receipts = []
        for note in changes.get("notes") or []:
            if isinstance(note, str) and note.strip() and self._add_note(note.strip()):
                receipts.append(f"📝 Saved note: {note.strip()}")
        new_tasks = [task for task in changes.get("new_tasks") or []
                     if isinstance(task, dict) and str(task.get("text", "")).strip()]
        for new_task in new_tasks:
            due = parse_due(new_task.get("due", ""))
            if len(new_tasks) == 1:
                # Plain-Python date reading is more reliable than the model when it finds a time.
                due = guess_due(message) or due
            task = self._add_task(str(new_task["text"]).strip(), due)
            receipts.append(f"✅ Added task: {task_label(task)}")
        for number in changes.get("finished_task_numbers") or []:
            tasks = self.data["tasks"]
            if isinstance(number, int) and 1 <= number <= len(tasks) and not tasks[number - 1]["done"]:
                task = self._finish_task(number)
                receipts.append(f"🎉 Finished task: {task['text']}")
        return receipts

    def _abilities(self):
        """What Maja can do, so she answers questions about herself correctly
        instead of guessing that she's 'just a text-based AI'."""
        speaking = ("Spoken replies are ON right now, so Matty hears your answers out loud"
                    if self.setting("speak", False) else
                    "Spoken replies are OFF right now; Matty can turn them on with the 🔊 button")
        seeing = ("The camera is OFF right now, so you can't see anything at the moment; Matty "
                  "can turn it on with 📷 and then send a message about what it shows")
        return (
            "What you (Maja) can do in this app: Matty can talk to you with the 🎤 button "
            "(speech is turned into text on this computer). You can speak your replies out "
            f"loud with a Windows voice. {speaking}. You can look through the webcam, but "
            f"only when Matty turns it on with 📷. {seeing}. You can answer questions from Matty's own documents "
            "(📂). Matty can chat with you from their phone (📱). You run on Matty's own "
            "computer. For hard questions Matty can start a message with 'think:' (or press "
            "🧠) to ask a much stronger cloud model, Claude, and with 'search:' Claude also "
            "searches the web. You can't browse the web yourself, so for current information "
            "suggest 'search:'. If Matty's calendar is connected, it's shown below and you can "
            "read it, but not change it. For bigger tasks (a plan, a report, a study schedule) "
            "Matty can start a message with 'job:'; you then work on it in the background with a "
            "planner, researcher, writer and reviewer, and save a report. Never say you are only "
            "text-based or can't speak."
        )

    def _search_documents(self, message):
        if self.library is None:
            return []
        try:
            return self.library.search(message)
        except (BrainError, LibraryError):
            return []  # Maja still answers, just without the documents.

    def _system_prompt(self, saved_before, receipts, excerpts):
        # Without the icon: the model can read the check mark as "completed".
        just_saved = "\n".join("- " + receipt.split(" ", 1)[1] for receipt in receipts) or "(nothing)"
        documents = ""
        if excerpts:
            documents = (
                "\n\nExcerpts from Matty's own documents that may help with the latest "
                "message, most relevant first. If they answer it, base your answer on them "
                "and name the source in square brackets, like [notes.pdf p. 3]. If they "
                "don't contain the answer, say so and make clear that the rest of your "
                "answer is general knowledge, not from the documents.\n\n"
                + "\n\n".join(f"[{source_label(hit['source'], hit['page'])}]\n{hit['text']}"
                              for hit in excerpts)
            )
        return (
            "You are Maja, Matty's warm, encouraging personal assistant. You help with "
            "English practice, studies, fitness, planning, meditation, finances, travel, "
            "and personal goals, and you can talk about any topic. Answer questions "
            "directly and helpfully. Keep replies short and friendly (a few sentences) "
            "unless Matty asks for more. If Matty writes in English, gently point out "
            "important mistakes when it helps them practice. Only mention past "
            "conversations or saved items that actually appear below or in this chat; "
            "never invent them. For money or health topics, give general guidance, not "
            "professional advice.\n\n"
            "Maja's app keeps a list of notes and tasks. Tasks with a ⏰ time get a "
            "notification at that time, but only while the Maja app is open. Tasks without "
            "a time get no reminder.\n\n"
            f"{self._abilities()}\n\n"
            f"Current local time: {datetime.now():%A %Y-%m-%d %H:%M}\n"
            f"{saved_before}{self._calendar_for_prompt()}\n\n"
            "Just saved for the first time from Matty's latest message (Matty already "
            f"sees this, so don't repeat it, and it is new, not something said before):\n{just_saved}"
            f"{documents}"
        )

    def _ask_brain(self, message, on_text=None, mode=None):
        """mode None: the local model. "cloud": Claude. "web": Claude with web search."""
        saved_before = self._notes_and_tasks()
        try:
            receipts = self._remember(message)
        except BrainError:
            # Nothing is saved this time. If Ollama itself is down, the reply below
            # fails too and shows Matty what to do.
            receipts = []
        excerpts = self._search_documents(message)
        user_message = {"role": "user", "content": message}
        messages = [
            {"role": "system", "content": self._system_prompt(saved_before, receipts, excerpts)},
            *self.history, user_message,
        ]
        try:
            if mode is None and on_text is None:
                answer = self.brain.chat(messages)
            else:
                stream = (self.cloud.chat_stream(messages, web=mode == "web") if mode
                          else self.brain.chat_stream(messages))
                pieces = []
                for piece in stream:
                    pieces.append(piece)
                    if on_text is not None:
                        on_text(piece)
                answer = "".join(pieces).strip()
                if not answer:
                    raise BrainError("The model sent an empty reply. Please try again.")
        except BrainError as error:
            return "\n".join([str(error), *receipts])
        if mode:
            spent = self.cloud.spent_this_month()
            receipts.append(f"☁️ {'Searched the web and answered' if mode == 'web' else 'Answered'} "
                            f"with Claude: ${self.cloud.last_cost:.3f} "
                            f"(${spent:.2f} of ${self.cloud.budget:.2f} this month)")
        used = [hit for hit in excerpts
                if hit["distance"] <= CLEAR_MATCH or source_label(hit["source"], hit["page"]) in answer]
        if used:
            sources = dict.fromkeys(source_label(hit["source"], hit["page"]) for hit in used)
            receipts.append("📚 Sources: " + ", ".join(sources))
        text = "\n".join([answer, *receipts])
        self.history += [user_message, {"role": "assistant", "content": answer}]
        del self.history[:-HISTORY_LIMIT]
        self._save()
        return text
