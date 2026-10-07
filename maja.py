"""Local command and memory layer for the Maja prototype."""

import json
from datetime import datetime
from pathlib import Path

from brain import BrainError


# How many recent chat messages Maja keeps in mind (and saves) between conversations.
HISTORY_LIMIT = 20

# The shape of the answer when Maja asks the model what to save from a message.
MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {"type": "array", "items": {"type": "string"}},
        "new_tasks": {"type": "array", "items": {"type": "string"}},
        "finished_task_numbers": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["notes", "new_tasks", "finished_task_numbers"],
}

MEMORY_RULES = """You update Matty's personal notes and to-do list. Read ONLY Matty's latest message and decide what to store.

Rules:
- notes: only lasting personal facts, goals, or preferences that Matty states about themself, or things Matty explicitly asks to remember. Write them as short sentences about Matty.
- new_tasks: only things Matty asks to be reminded of or says they need to do. Start with a verb and keep any day or time Matty mentions.
- finished_task_numbers: numbers of open tasks from the list that Matty says they completed.
- Questions, greetings, small talk and requests for information or advice store NOTHING.
- Don't repeat notes that are already saved.
- When unsure, store nothing. Most messages store nothing.

Examples:
"How are you?" -> {"notes": [], "new_tasks": [], "finished_task_numbers": []}
"What should I eat before an exam?" -> {"notes": [], "new_tasks": [], "finished_task_numbers": []}
"Translate 'thank you' to French" -> {"notes": [], "new_tasks": [], "finished_task_numbers": []}
"I love hiking on weekends" -> {"notes": ["Matty loves hiking on weekends"], "new_tasks": [], "finished_task_numbers": []}
"Remind me to email my teacher on Friday" -> {"notes": [], "new_tasks": ["Email my teacher on Friday"], "finished_task_numbers": []}
With open task "2. Pay rent": "I paid the rent" -> {"notes": [], "new_tasks": [], "finished_task_numbers": [2]}
"""


class Maja:
    def __init__(self, data_file: Path, brain=None):
        self.data_file = Path(data_file)
        self.data = self._load()
        self.brain = brain
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

    def _add_note(self, note):
        if note.lower().rstrip(".") in (n.lower().rstrip(".") for n in self.data["notes"]):
            return False
        self.data["notes"].append(note)
        self._save()
        return True

    def _add_task(self, text):
        self.data["tasks"].append({"text": text, "done": False})
        self._save()

    def _finish_task(self, number):
        """Mark task `number` (1-based) as done and return it, or None if it doesn't exist."""
        if not 1 <= number <= len(self.data["tasks"]):
            return None
        task = self.data["tasks"][number - 1]
        task["done"] = True
        self._save()
        return task

    def reply(self, message: str) -> str:
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
                    "'show tasks', 'finish task 1', 'what time is it?', or 'new chat'.")
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
                f"{i}. {'✓' if task['done'] else '○'} {task['text']}"
                for i, task in enumerate(tasks, 1))) if tasks else "Your task list is empty."
        if lower.startswith("finish task "):
            try:
                task = self._finish_task(int(lower[len("finish task "):].strip()))
            except ValueError:
                task = None
            if task is None:
                return "Use a task number from 'show tasks', for example 'finish task 1'."
            return f"Done! {task['text']} 🎉"
        if lower in {"what time is it?", "time"}:
            return f"Your computer's local time is {datetime.now():%H:%M}."
        if self.brain is None:
            return ("I can't answer open-ended questions yet. Type 'help' to see what "
                    "this first version can do. We'll add an AI conversation engine next.")
        return self._ask_brain(message)

    def _notes_and_tasks(self):
        notes = "\n".join(f"- {note}" for note in self.data["notes"]) or "(none yet)"
        tasks = "\n".join(
            f"{i}. {task['text']}" for i, task in enumerate(self.data["tasks"], 1) if not task["done"]
        ) or "(none)"
        return f"Saved notes about Matty:\n{notes}\nMatty's open tasks:\n{tasks}"

    def _remember(self, message):
        """Step 1: ask the model what (if anything) to save from Matty's message.

        Returns receipts like '📝 Saved note: ...' for everything that was saved.
        """
        context = ""
        if self.history and self.history[-1]["role"] == "assistant":
            context = f"Maja's previous message (context only, store nothing from it):\n{self.history[-1]['content']}\n\n"
        changes = self.brain.extract([
            {"role": "system", "content": MEMORY_RULES + "\n" + self._notes_and_tasks()},
            {"role": "user", "content": f"{context}Matty's latest message:\n{message}"},
        ], MEMORY_SCHEMA)

        receipts = []
        for note in changes.get("notes") or []:
            if isinstance(note, str) and note.strip() and self._add_note(note.strip()):
                receipts.append(f"📝 Saved note: {note.strip()}")
        for text in changes.get("new_tasks") or []:
            if isinstance(text, str) and text.strip():
                self._add_task(text.strip())
                receipts.append(f"✅ Added task: {text.strip()}")
        for number in changes.get("finished_task_numbers") or []:
            tasks = self.data["tasks"]
            if isinstance(number, int) and 1 <= number <= len(tasks) and not tasks[number - 1]["done"]:
                task = self._finish_task(number)
                receipts.append(f"🎉 Finished task: {task['text']}")
        return receipts

    def _system_prompt(self, saved_before, receipts):
        # Without the emoji: the model can read "✅" as "completed".
        just_saved = "\n".join("- " + receipt.split(" ", 1)[1] for receipt in receipts) or "(nothing)"
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
            "Maja's app keeps a list of notes and tasks. It can't send reminders or "
            "notifications, so never promise them.\n\n"
            f"Current local time: {datetime.now():%A %Y-%m-%d %H:%M}\n"
            f"{saved_before}\n\n"
            "Just saved for the first time from Matty's latest message (Matty already "
            f"sees this, so don't repeat it, and it is new, not something said before):\n{just_saved}"
        )

    def _ask_brain(self, message):
        saved_before = self._notes_and_tasks()
        try:
            receipts = self._remember(message)
        except BrainError:
            # Nothing is saved this time. If Ollama itself is down, the reply below
            # fails too and shows Matty what to do.
            receipts = []
        user_message = {"role": "user", "content": message}
        try:
            answer = self.brain.chat([
                {"role": "system", "content": self._system_prompt(saved_before, receipts)},
                *self.history, user_message,
            ])
        except BrainError as error:
            return "\n".join([str(error), *receipts])
        text = "\n".join([answer, *receipts])
        self.history += [user_message, {"role": "assistant", "content": answer}]
        del self.history[:-HISTORY_LIMIT]
        self._save()
        return text
