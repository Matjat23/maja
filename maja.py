"""Local command and memory layer for the Maja prototype."""

import json
from datetime import datetime
from pathlib import Path

from brain import BrainError


# How many recent chat messages Maja keeps in mind during a conversation.
HISTORY_LIMIT = 20


class Maja:
    def __init__(self, data_file: Path, brain=None):
        self.data_file = Path(data_file)
        self.data = self._load()
        self.brain = brain
        self.history = []

    def _load(self):
        if not self.data_file.exists():
            return {"notes": [], "tasks": []}
        try:
            data = json.loads(self.data_file.read_text(encoding="utf-8"))
            if not isinstance(data.get("notes"), list) or not isinstance(data.get("tasks"), list):
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

    def reply(self, message: str) -> str:
        message = message.strip()
        lower = message.lower()
        if not message:
            return "Type a message and I'll be here. 💜"
        if lower in {"hello", "hi", "hey"}:
            return "Hi Matty! I'm Maja. What would you like to work on today? 💜"
        if lower in {"help", "commands"}:
            return ("Try: 'remember ...', 'what do you remember?', 'add task ...', "
                    "'show tasks', 'finish task 1', 'what time is it?', or 'new chat'. "
                    "Anything else, I'll answer like a normal conversation.")
        if lower in {"new chat", "reset chat"}:
            self.history.clear()
            return "Fresh start! Your notes and tasks are still saved. 💜"
        if lower.startswith("remember "):
            note = message[9:].strip()
            if not note:
                return "Tell me what you would like me to remember."
            self.data["notes"].append(note)
            self._save()
            return "I'll remember that. 💜"
        if lower in {"what do you remember?", "show notes", "my notes"}:
            notes = self.data["notes"]
            return "Your notes:\n" + "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1)) if notes else "I don't have any notes yet."
        if lower.startswith("add task "):
            task = message[9:].strip()
            if not task:
                return "Tell me the task you want to add."
            self.data["tasks"].append({"text": task, "done": False})
            self._save()
            return f"Added task: {task}"
        if lower in {"show tasks", "my tasks"}:
            tasks = self.data["tasks"]
            return ("Your tasks:\n" + "\n".join(
                f"{i}. {'✓' if task['done'] else '○'} {task['text']}"
                for i, task in enumerate(tasks, 1))) if tasks else "Your task list is empty."
        if lower.startswith("finish task "):
            try:
                number = int(lower[len("finish task "):].strip())
                if number < 1:
                    raise ValueError
                task = self.data["tasks"][number - 1]
            except (ValueError, IndexError):
                return "Use a task number from 'show tasks', for example 'finish task 1'."
            task["done"] = True
            self._save()
            return f"Done! {task['text']} 🎉"
        if lower in {"what time is it?", "time"}:
            return f"Your computer's local time is {datetime.now():%H:%M}."
        if self.brain is None:
            return ("I can't answer open-ended questions yet. Type 'help' to see what "
                    "this first version can do. We'll add an AI conversation engine next.")
        return self._ask_brain(message)

    def _system_prompt(self):
        notes = "\n".join(f"- {note}" for note in self.data["notes"]) or "(none yet)"
        tasks = "\n".join(
            f"- {task['text']}" for task in self.data["tasks"] if not task["done"]
        ) or "(none)"
        return (
            "You are Maja, Matty's warm, encouraging personal assistant. You help with "
            "English practice, studies, fitness, planning, meditation, finances, travel, "
            "and personal goals. Keep replies short and friendly (a few sentences) unless "
            "Matty asks for more. If Matty writes in English, gently point out important "
            "mistakes when it helps them practice.\n\n"
            "You cannot save anything yourself. If Matty wants something remembered or added "
            "as a task, tell them to type 'remember ...' or 'add task ...'. Never claim you "
            "saved something. For money or health topics, give general guidance, not "
            "professional advice.\n\n"
            f"Current local time: {datetime.now():%A %Y-%m-%d %H:%M}\n"
            f"Things Matty asked you to remember:\n{notes}\n"
            f"Matty's open tasks:\n{tasks}"
        )

    def _ask_brain(self, message):
        user_message = {"role": "user", "content": message}
        messages = [{"role": "system", "content": self._system_prompt()},
                    *self.history, user_message]
        try:
            answer = self.brain.chat(messages)
        except BrainError as error:
            return str(error)
        self.history += [user_message, {"role": "assistant", "content": answer}]
        del self.history[:-HISTORY_LIMIT]
        return answer
