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

    def __init__(self, answer="Hello from the model", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.answer


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


if __name__ == "__main__":
    unittest.main()
