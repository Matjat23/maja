import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
