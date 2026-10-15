import math
import re
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from brain import BrainError
from library import CHUNK_CHARS, LIBRARY_AVAILABLE, Library, split_into_chunks
from maja import Maja
from reminders import guess_due, parse_due


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

    def embed(self, texts):
        """A simple stand-in for an embedding model: texts sharing words get similar vectors."""
        vectors = []
        for text in texts:
            vector = [0.0] * 64
            for word in re.findall(r"[a-z]+", text.lower().split(":", 1)[-1]):
                if len(word) > 3:
                    vector[zlib.crc32(word.encode()) % 64] += 1.0
            length = math.sqrt(sum(x * x for x in vector)) or 1.0
            vectors.append([x / length for x in vector] if any(vector) else [1.0] + [0.0] * 63)
        return vectors

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
                                              "new_tasks": [{"text": "Buy running shoes", "due": ""}]})
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
            brain = FakeBrain("Okay.", {"notes": [" ", 5], "new_tasks": [{"text": "", "due": ""}, "not a task"],
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


class ReminderTests(unittest.TestCase):
    def test_tasks_with_a_time_get_a_due_date(self):
        with tempfile.TemporaryDirectory() as folder:
            due = (datetime.now() + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
            brain = FakeBrain("Sure!", {"new_tasks": [{"text": "Call mom", "due": f"{due:%Y-%m-%d %H:%M}"}]})
            assistant = Maja(Path(folder) / "data.json", brain)
            answer = assistant.reply("Remind me to call mom in two days at 6pm")
            self.assertIn(f"✅ Added task: Call mom (⏰ {due:%a %d %b}, 18:00)", answer)
            self.assertEqual(assistant.data["tasks"][0]["due"], f"{due:%Y-%m-%d} 18:00")
            self.assertIn("○ Call mom (⏰", assistant.reply("show tasks"))

    def test_bad_or_past_due_dates_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("Ok.", {"new_tasks": [{"text": "A", "due": "next week"},
                                                     {"text": "B", "due": "2000-01-01 09:00"}]})
            assistant = Maja(Path(folder) / "data.json", brain)
            assistant.reply("stuff")
            self.assertEqual(assistant.data["tasks"], [{"text": "A", "done": False},
                                                       {"text": "B", "done": False}])

    def test_due_reminders_fire_once_and_skip_done_tasks(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json")
            now = datetime(2026, 10, 10, 18, 0)
            assistant.data["tasks"] = [
                {"text": "Due now", "done": False, "due": "2026-10-10 18:00"},
                {"text": "Later", "done": False, "due": "2026-10-10 19:00"},
                {"text": "Already done", "done": True, "due": "2026-10-10 17:00"},
                {"text": "No time", "done": False},
            ]
            self.assertEqual([t["text"] for t in assistant.due_reminders(now)], ["Due now"])
            self.assertEqual(assistant.due_reminders(now), [])
            # "Reminded" is saved, so a restart doesn't remind again.
            self.assertEqual(Maja(Path(folder) / "data.json").due_reminders(now), [])

    def test_parse_due(self):
        now = datetime(2026, 10, 6, 12, 0)
        self.assertEqual(parse_due("2026-10-10 18:00", now), datetime(2026, 10, 10, 18, 0))
        self.assertIsNone(parse_due("2026-10-06 11:00", now))
        self.assertIsNone(parse_due("", now))
        self.assertIsNone(parse_due("Saturday", now))

    def test_guess_due_understands_common_phrases(self):
        now = datetime(2026, 10, 6, 14, 30)  # A Tuesday afternoon.
        cases = {
            "remind me in 2 hours to drink water": datetime(2026, 10, 6, 16, 30),
            "in half an hour": datetime(2026, 10, 6, 15, 0),
            "remind me at 7am to meditate": datetime(2026, 10, 7, 7, 0),
            "at 6pm call mom": datetime(2026, 10, 6, 18, 0),
            "at 18:45": datetime(2026, 10, 6, 18, 45),
            "tomorrow at 8": datetime(2026, 10, 7, 8, 0),
            "call grandma tomorrow": datetime(2026, 10, 7, 9, 0),
            "finish the report by Friday": datetime(2026, 10, 9, 9, 0),
            "on Tuesday": datetime(2026, 10, 13, 9, 0),
            "Saturday at 6 in the evening": datetime(2026, 10, 10, 18, 0),
            "tonight": datetime(2026, 10, 6, 19, 0),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(guess_due(message, now), expected)
        for message in ["I need to buy milk", "I study at Chalmers", "at 99"]:
            with self.subTest(message=message):
                self.assertIsNone(guess_due(message, now))


class ChunkingTests(unittest.TestCase):
    def test_short_paragraphs_are_kept_together(self):
        self.assertEqual(split_into_chunks("First idea.\n\nSecond   idea.\n\n\n"), ["First idea. Second idea."])

    def test_long_text_is_split_with_overlap(self):
        words = " ".join(f"word{i}" for i in range(600))
        chunks = split_into_chunks(words)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= CHUNK_CHARS for chunk in chunks))
        # The end of one chunk is repeated at the start of the next, so no idea is cut in half.
        self.assertIn(chunks[0].split()[-1], chunks[1])
        self.assertIn("word599", chunks[-1])


class FakeLibrary:
    folder = Path("documents")

    def __init__(self, hits=()):
        self.hits = list(hits)

    def search(self, question):
        return self.hits

    def documents(self):
        return [hit["source"] for hit in self.hits]


class MajaDocumentTests(unittest.TestCase):
    def test_answers_use_document_excerpts_and_show_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("MPI passes messages between processes [hpc.pdf p. 3].")
            library = FakeLibrary([
                {"source": "hpc.pdf", "page": 3, "text": "MPI sends messages between processes.", "distance": 0.2},
                {"source": "hpc.pdf", "page": 3, "text": "More on MPI.", "distance": 0.3},
                {"source": "notes.md", "page": None, "text": "Lab notes about MPI.", "distance": 0.4},
            ])
            assistant = Maja(Path(folder) / "data.json", brain, library)
            answer = assistant.reply("How does MPI work?")
            self.assertTrue(answer.endswith("📚 Sources: hpc.pdf p. 3, notes.md"))
            self.assertIn("MPI sends messages between processes.", brain.calls[0][0]["content"])
            # Sources aren't stored in the chat history, only the answer.
            self.assertNotIn("📚", assistant.history[-1]["content"])

    def test_weak_matches_are_only_shown_when_the_answer_uses_them(self):
        weak = {"source": "spanish.md", "page": None, "text": "Buenos días.", "distance": 0.43}
        with tempfile.TemporaryDirectory() as folder:
            unused = Maja(Path(folder) / "a.json", FakeBrain("Try oatmeal."), FakeLibrary([weak]))
            self.assertEqual(unused.reply("Good breakfast?"), "Try oatmeal.")
            cited = Maja(Path(folder) / "b.json", FakeBrain("Buenos días [spanish.md]."), FakeLibrary([weak]))
            self.assertIn("📚 Sources: spanish.md", cited.reply("Good morning in Spanish?"))

    def test_no_sources_line_without_matching_documents(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json", FakeBrain("Hi!"), FakeLibrary())
            self.assertEqual(assistant.reply("How are you?"), "Hi!")

    def test_document_commands_explain_setup_when_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json", FakeBrain())
            self.assertIn("pip install", assistant.reply("show documents"))


@unittest.skipUnless(LIBRARY_AVAILABLE, "needs chromadb and pypdf (requirements.txt)")
class LibraryTests(unittest.TestCase):
    def setUp(self):
        # Chroma keeps its files open on Windows, so the folder can't always be removed.
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name)
        self.docs = self.root / "documents"
        self.docs.mkdir()
        (self.docs / "hpc.txt").write_text(
            "MPI processes exchange messages across distributed memory nodes.", encoding="utf-8")
        (self.docs / "home.md").write_text("The rent payment is due monthly.", encoding="utf-8")
        (self.docs / "photo.jpg").write_bytes(b"not a document")

    def tearDown(self):
        self.temporary.cleanup()

    def library(self):
        return Library(self.docs, self.root / "database", FakeBrain())

    def test_reads_finds_and_forgets_documents(self):
        library = self.library()
        self.assertEqual(sorted(library.update()["added"]), ["home.md", "hpc.txt"])
        self.assertEqual(library.documents(), ["home.md", "hpc.txt"])

        hits = library.search("How do MPI processes exchange messages?")
        self.assertEqual(hits[0]["source"], "hpc.txt")
        self.assertNotIn("home.md", [hit["source"] for hit in hits])
        self.assertEqual(library.search("favourite pizza topping"), [])

        # Unchanged files aren't read again, even after a restart.
        self.assertEqual(self.library().update(), {"added": [], "updated": [], "removed": [], "problems": []})

        (self.docs / "home.md").write_text("The rent payment is due on the first weekday.", encoding="utf-8")
        (self.docs / "hpc.txt").unlink()
        report = library.update()
        self.assertEqual((report["updated"], report["removed"]), (["home.md"], ["hpc.txt"]))
        self.assertEqual(library.search("MPI processes exchange messages"), [])

    def test_broken_pdfs_are_reported_not_fatal(self):
        (self.docs / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
        report = self.library().update()
        self.assertEqual(len(report["problems"]), 1)
        self.assertIn("broken.pdf", report["problems"][0])
        self.assertEqual(sorted(report["added"]), ["home.md", "hpc.txt"])


if __name__ == "__main__":
    unittest.main()
