import tempfile
import unittest
from pathlib import Path

from brain import BrainError
from maja import Maja


class MajaStorageTests(unittest.TestCase):
    def test_notes_and_tasks_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            assistant = Maja(path)
            assistant.reply("remember My project is Maja")
            assistant.reply("add task Sketch a robot")
            assistant.reply("finish task 1")
            restarted = Maja(path)
            self.assertIn("My project is Maja", restarted.reply("show notes"))
            self.assertIn("✓ Sketch a robot", restarted.reply("show tasks"))

    def test_bad_task_number_does_not_change_data(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json")
            self.assertIn("task number", assistant.reply("finish task 1"))
            self.assertEqual(assistant.reply("show tasks"), "Your task list is empty.")


class FakeBrain:
    """Stands in for Ollama so tests run without a model."""

    def __init__(self, answer="Hello from the model", saves=None, error=None, save_error=None):
        self.answer = answer
        # What the model "decides" to save from each message.
        self.saves = saves or {}
        self.error = error
        self.save_error = save_error
        self.calls = []
        self.save_calls = []

    def chat(self, messages):
        self.calls.append(list(messages))
        if self.error:
            raise self.error
        return self.answer

    def extract(self, messages, schema):
        self.save_calls.append(list(messages))
        if self.error or self.save_error:
            raise self.error or self.save_error
        return {"notes": [], "new_tasks": [], "finished_task_numbers": [], **self.saves}

class MajaConversationTests(unittest.TestCase):
    def test_open_questions_go_to_the_brain_with_notes_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain()
            assistant = Maja(Path(folder) / "data.json", brain)
            assistant.reply("remember I study at Chalmers")
            assistant.reply("add task Practice English")
            self.assertEqual(assistant.reply("How should I plan my week?"), "Hello from the model")
            assistant.reply("And next week?")
            system, *rest = brain.calls[-1]
            self.assertIn("I study at Chalmers", system["content"])
            self.assertIn("Practice English", system["content"])
            self.assertEqual([m["content"] for m in rest],
                             ["How should I plan my week?", "Hello from the model", "And next week?"])

    def test_commands_do_not_use_the_brain(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain()
            assistant = Maja(Path(folder) / "data.json", brain)
            assistant.reply("show tasks")
            assistant.reply("remember something")
            self.assertEqual(brain.calls, [])

    def test_brain_errors_are_shown_and_not_kept_in_history(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain(error=BrainError("I can't reach Ollama."))
            assistant = Maja(Path(folder) / "data.json", brain)
            self.assertEqual(assistant.reply("Hi there, how are you?"), "I can't reach Ollama.")
            self.assertEqual(assistant.history, [])

    def test_new_chat_clears_history_but_keeps_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json", FakeBrain())
            assistant.reply("remember keep me")
            assistant.reply("Tell me a joke")
            assistant.reply("new chat")
            self.assertEqual(assistant.history, [])
            self.assertIn("keep me", assistant.reply("show notes"))

    def test_conversation_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            Maja(path, FakeBrain("Nice to meet you!")).reply("My name is Matty")
            brain = FakeBrain()
            Maja(path, brain).reply("What is my name?")
            self.assertIn({"role": "user", "content": "My name is Matty"}, brain.calls[0])
            self.assertIn({"role": "assistant", "content": "Nice to meet you!"}, brain.calls[0])

    def test_old_data_files_without_history_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.json"
            path.write_text('{"notes": ["old note"], "tasks": []}', encoding="utf-8")
            assistant = Maja(path, FakeBrain())
            self.assertEqual(assistant.history, [])
            self.assertIn("old note", assistant.reply("show notes"))



class MajaSavingTests(unittest.TestCase):
    def test_notes_and_tasks_from_conversation_are_saved_with_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("Great goal!", {"notes": ["Wants to run 5 km"],
                                              "new_tasks": ["Buy running shoes"]})
            assistant = Maja(Path(folder) / "data.json", brain)
            answer = assistant.reply("I want to run 5 km, I need shoes")
            self.assertEqual(answer, "Great goal!\n📝 Saved note: Wants to run 5 km\n"
                                     "✅ Added task: Buy running shoes")
            self.assertIn("Wants to run 5 km", assistant.reply("show notes"))
            self.assertIn("○ Buy running shoes", assistant.reply("show tasks"))
            # The reply step is told what was saved, so it doesn't contradict it.
            self.assertIn("Buy running shoes", brain.calls[0][0]["content"])

    def test_finishing_a_task(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json",
                             FakeBrain("Well done!", {"finished_task_numbers": [1]}))
            assistant.reply("add task Call mom")
            self.assertIn("🎉 Finished task: Call mom", assistant.reply("I called mom"))
            self.assertIn("✓ Call mom", assistant.reply("show tasks"))
            # A task that is already done isn't "finished" again.
            self.assertEqual(assistant.reply("I called mom again"), "Well done!")

    def test_bad_save_suggestions_change_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("Okay.", {"notes": [" ", 5], "new_tasks": [""],
                                        "finished_task_numbers": [7, 0, "1"]})
            assistant = Maja(Path(folder) / "data.json", brain)
            self.assertEqual(assistant.reply("hmm"), "Okay.")
            self.assertEqual(assistant.data["notes"], [])
            self.assertEqual(assistant.data["tasks"], [])

    def test_duplicate_notes_are_not_saved_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json",
                             FakeBrain("Noted.", {"notes": ["Likes tea."]}))
            assistant.reply("remember likes tea")
            self.assertEqual(assistant.reply("I like tea"), "Noted.")
            self.assertEqual(assistant.data["notes"], ["likes tea"])

    def test_maja_still_answers_when_saving_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("Here's an answer.", save_error=BrainError("unreadable"))
            assistant = Maja(Path(folder) / "data.json", brain)
            self.assertEqual(assistant.reply("Question?"), "Here's an answer.")

    def test_save_step_sees_previous_reply_as_context(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("Want me to remind you to stretch?")
            assistant = Maja(Path(folder) / "data.json", brain)
            assistant.reply("My legs hurt after running")
            assistant.reply("yes please")
            self.assertIn("Want me to remind you to stretch?", brain.save_calls[1][1]["content"])


if __name__ == "__main__":
    unittest.main()
