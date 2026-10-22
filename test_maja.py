import math
import re
import tempfile
import threading
import types
import unittest
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from brain import BrainError
from camera import CAMERA_AVAILABLE, photo_for_model, preview_png
from library import CHUNK_CHARS, LIBRARY_AVAILABLE, Library, split_into_chunks
from agenda import AGENDA_AVAILABLE, Agenda, describe_event
from cloud import CLOUD_AVAILABLE, CloudBrain
from jobs import JobRunner
from markdown import markdown_runs, plain_text
from maja import Maja, cloud_mode
from server import SERVER_AVAILABLE, create_app
from voice import (VOICE_INPUT_AVAILABLE, Speaker, WakeListener, parse_wake, split_sentences,
                   text_for_speech)
from reminders import guess_due, guess_repeat, next_due, parse_due


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

    def chat_stream(self, messages):
        """Hands out the answer word by word, like a real model writing it."""
        answer = self.chat(messages)
        words = answer.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else word + " "

    def see(self, messages, photo):
        self.calls.append(list(messages) + [{"photo": photo}])
        if self.error:
            raise self.error
        return self.answer

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


class AutostartTests(unittest.TestCase):
    def test_start_with_windows_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json")
            with mock.patch("autostart.enable", return_value=True) as enable:
                self.assertIn("every time you log in", assistant.reply("start with windows"))
                enable.assert_called_once()
            with mock.patch("autostart.enable", return_value=False):
                self.assertIn("couldn't", assistant.reply("start with windows"))
            with mock.patch("autostart.disable") as disable:
                self.assertIn("won't start", assistant.reply("stop starting with windows"))
                disable.assert_called_once()


@unittest.skipUnless(SERVER_AVAILABLE, "needs fastapi and httpx (requirements.txt)")
class PhoneServerTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        self.temporary = tempfile.TemporaryDirectory()
        self.maja = Maja(Path(self.temporary.name) / "data.json", FakeBrain("Hi from Maja!"))
        self.seen = []
        app = create_app(self.maja, threading.Lock(), "secret-key", lambda m, r: self.seen.append((m, r)))
        self.client = TestClient(app)

    def tearDown(self):
        self.temporary.cleanup()

    def test_page_loads_without_a_key_but_contains_no_data(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Maja", response.text)

    def test_api_needs_the_right_key(self):
        self.assertEqual(self.client.get("/api/history").status_code, 401)
        self.assertEqual(self.client.post("/api/chat", json={"message": "hi there"},
                                          headers={"X-Maja-Key": "wrong"}).status_code, 401)

    def test_chat_from_phone_reaches_maja_and_the_window(self):
        headers = {"X-Maja-Key": "secret-key"}
        response = self.client.post("/api/chat", json={"message": "Hello from my phone"}, headers=headers)
        self.assertEqual(response.json(), {"reply": "Hi from Maja!"})
        self.assertEqual(self.seen, [("Hello from my phone", "Hi from Maja!")])
        history = self.client.get("/api/history", headers=headers).json()["messages"]
        self.assertEqual(history[0], {"role": "user", "content": "Hello from my phone"})
        # Commands work from the phone too.
        self.client.post("/api/chat", json={"message": "add task Water the plants"}, headers=headers)
        self.assertEqual(self.maja.data["tasks"][0]["text"], "Water the plants")


class AbilityTests(unittest.TestCase):
    def test_maja_is_told_what_she_can_do_and_whether_she_is_speaking(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain()
            assistant = Maja(Path(folder) / "data.json", brain)
            assistant.reply("Can you talk?")
            self.assertIn("Spoken replies are OFF", brain.calls[-1][0]["content"])
            assistant.change_setting("speak", True)
            assistant.reply("Can you talk now?")
            self.assertIn("Spoken replies are ON", brain.calls[-1][0]["content"])
            self.assertIn("webcam", brain.calls[-1][0]["content"])


class StreamingTests(unittest.TestCase):
    def test_reply_arrives_in_pieces_and_ends_with_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("Sure, I will remind you.", {"new_tasks": [{"text": "Call mom", "due": ""}]})
            assistant = Maja(Path(folder) / "data.json", brain)
            pieces = []
            answer = assistant.reply("Remind me to call mom", on_text=pieces.append)
            self.assertEqual(pieces, ["Sure, ", "I ", "will ", "remind ", "you."])
            self.assertEqual(answer, "Sure, I will remind you.\n✅ Added task: Call mom")
            self.assertEqual(assistant.history[-1]["content"], "Sure, I will remind you.")

    def test_commands_answer_at_once_without_pieces(self):
        with tempfile.TemporaryDirectory() as folder:
            pieces = []
            assistant = Maja(Path(folder) / "data.json", FakeBrain())
            assistant.reply("show tasks", on_text=pieces.append)
            self.assertEqual(pieces, [])

    def test_errors_while_streaming_are_shown(self):
        with tempfile.TemporaryDirectory() as folder:
            assistant = Maja(Path(folder) / "data.json", FakeBrain(error=BrainError("I can't reach Ollama.")))
            self.assertEqual(assistant.reply("Hi there!", on_text=lambda piece: None), "I can't reach Ollama.")


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.brain = FakeBrain("Good morning, Matty! You've got this.")
        self.maja = Maja(Path(self.temporary.name) / "data.json", self.brain)
        self.maja.data["notes"] = ["Matty wants to run 5 km by spring"]
        self.maja.data["tasks"] = [
            {"text": "Call mom", "done": False, "due": "2026-10-19 18:00"},
            {"text": "Gym", "done": False, "due": "2026-10-19 07:30"},
            {"text": "Pay rent", "done": False, "due": "2026-10-17 09:00"},
            {"text": "Hand in essay", "done": False, "due": "2026-10-22 09:00"},
            {"text": "Next month", "done": False, "due": "2026-11-20 09:00"},
            {"text": "Old done task", "done": True, "due": "2026-10-19 08:00"},
            {"text": "Buy milk", "done": False},
        ]
        self.now = datetime(2026, 10, 19, 7, 0)  # A Monday morning.

    def tearDown(self):
        self.temporary.cleanup()

    def test_briefing_lists_today_overdue_and_this_week(self):
        text = self.maja.morning_briefing(self.now)
        self.assertEqual(text, (
            "Good morning, Matty! You've got this.\n\n"
            "Today, Monday 19 October:\n"
            "⏰ 07:30 Gym\n"
            "⏰ 18:00 Call mom\n"
            "Overdue:\n"
            "⚠️ Pay rent (was due Sat 17 Oct, 09:00)\n"
            "Coming up this week:\n"
            "📅 Thu 22 Oct: Hand in essay\n"
            "📝 1 more open task without a date (type 'show tasks')."
        ))
        # The model gets the facts and Matty's goals to write the greeting.
        self.assertIn("run 5 km", self.brain.calls[-1][1]["content"])

    def test_briefing_is_due_once_per_day_from_5_am(self):
        self.assertFalse(self.maja.briefing_due(datetime(2026, 10, 19, 4, 59)))
        self.assertTrue(self.maja.briefing_due(self.now))
        self.maja.morning_briefing(self.now)
        self.assertFalse(self.maja.briefing_due(datetime(2026, 10, 19, 15, 0)))
        self.assertTrue(self.maja.briefing_due(datetime(2026, 10, 20, 6, 0)))

    def test_quiet_day_and_no_model(self):
        self.maja.brain = None
        self.maja.data["tasks"] = []
        self.assertEqual(self.maja.morning_briefing(datetime(2026, 10, 19, 19, 0)),
                         "Good evening, Matty! 💜\n\nToday, Monday 19 October:\nNothing is due today.")


class VoiceTests(unittest.TestCase):
    def test_wake_phrase(self):
        self.assertEqual(parse_wake("Hey Maja, what's my day?"), (True, "what's my day?"))
        self.assertEqual(parse_wake("Hey Maya. Remind me to drink water."), (True, "Remind me to drink water"))
        self.assertEqual(parse_wake("Okay Maja, show tasks."), (True, "show tasks"))
        self.assertEqual(parse_wake("Hey Maja."), (True, ""))
        self.assertEqual(parse_wake("I was talking to Maria yesterday."), (False, ""))
        self.assertEqual(parse_wake("Tell Maja I said hi."), (False, ""))

    def test_only_words_are_spoken(self):
        self.assertEqual(text_for_speech("**Great** job! 💜\n✅ Added task: Gym\n📚 Sources: notes.pdf p. 3"),
                         "Great job! \n Added task: Gym")
        self.assertEqual(split_sentences("Hi Matty! How are you?\nI'm fine."),
                         ["Hi Matty!", "How are you?", "I'm fine."])

    def test_streamed_reply_is_spoken_sentence_by_sentence(self):
        class RecordingSpeaker(Speaker):
            neural = True

            def say(self, text):
                said.append(text)

        said = []
        speaker = RecordingSpeaker()
        for piece in ["Good ", "morning, Matty", "! You have ", "two tasks", ". Want help", "?"]:
            speaker.feed(piece)
        self.assertEqual(said, ["Good morning, Matty!", "You have two tasks."])
        speaker.flush("\n✅ Added task: Gym")
        self.assertEqual(said[-1], "Want help?\n\n✅ Added task: Gym")

    @unittest.skipUnless(VOICE_INPUT_AVAILABLE, "needs numpy, sounddevice and faster-whisper")
    def test_microphone_sound_is_cut_into_pieces_of_speech(self):
        import queue as queue_module
        import numpy as np
        rate = 16000
        quiet = lambda seconds: np.zeros(int(rate * seconds), np.float32)
        loud = lambda seconds: (0.1 * np.sin(np.arange(int(rate * seconds)) * 0.2)).astype(np.float32)
        click = loud(0.1)  # Too short to be speech.
        audio = np.concatenate([quiet(1), loud(1.0), quiet(0.3), loud(0.5), quiet(1.2), click, quiet(1.5)])
        blocks = queue_module.Queue()
        for start in range(0, len(audio) - 480, 480):
            blocks.put(audio[start:start + 480])
        blocks.put(None)
        listener = WakeListener(None, None, None)
        pieces = list(listener.pieces_of_speech(blocks))
        # One piece: the short pause inside it doesn't split it, and the click is ignored.
        self.assertEqual(len(pieces), 1)
        self.assertGreater(len(pieces[0]) / rate, 1.8)


class FakeStream:
    """Stands in for client.beta.messages.stream(...)."""

    def __init__(self, pieces, usage, stop_reason="end_turn"):
        self.text_stream = iter(pieces)
        self.final = types.SimpleNamespace(usage=usage, stop_reason=stop_reason, content=[])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.final


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.maja = Maja(Path(self.temporary.name) / "data.json", FakeBrain("Local answer."))
        self.cloud = CloudBrain(self.maja)
        self.requests = []
        usage = types.SimpleNamespace(input_tokens=5000, output_tokens=1000,
                                      server_tool_use=types.SimpleNamespace(web_search_requests=2))

        def stream(**request):
            self.requests.append(request)
            return FakeStream(["Claude ", "says hi."], usage)

        self.cloud._client = types.SimpleNamespace(
            beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=stream)))
        self.maja.cloud = self.cloud
        self.env = mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def test_only_explicit_prefixes_go_to_the_cloud(self):
        self.assertEqual(cloud_mode("think: why is the sky blue?"), ("cloud", "why is the sky blue?"))
        self.assertEqual(cloud_mode("Think harder, plan my thesis"), ("cloud", "plan my thesis"))
        self.assertEqual(cloud_mode("search: weather in Gothenburg"), ("web", "weather in Gothenburg"))
        self.assertEqual(cloud_mode("Search the web for cheap flights"), ("web", "cheap flights"))
        for message in ["Think about my plan", "I think so", "search for my keys", "thinking: hm"]:
            with self.subTest(message=message):
                self.assertEqual(cloud_mode(message), (None, message))

    @unittest.skipUnless(CLOUD_AVAILABLE, "needs the anthropic package")
    def test_think_uses_claude_and_shows_the_cost(self):
        pieces = []
        answer = self.maja.reply("think: explain Amdahl's law", on_text=pieces.append)
        self.assertEqual(pieces, ["Claude ", "says hi."])
        # 5000 input tokens at $4/M + 1000 output at $20/M + 2 searches at $0.01 = $0.06.
        self.assertTrue(answer.endswith("☁️ Answered with Claude: $0.060 ($0.06 of $5.00 this month)"))
        request = self.requests[0]
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["messages"][-1], {"role": "user", "content": "explain Amdahl's law"})
        self.assertIn("You are Maja", request["system"])
        self.assertEqual(request["tools"], [])
        self.assertEqual(request["fallbacks"], "default")

    @unittest.skipUnless(CLOUD_AVAILABLE, "needs the anthropic package")
    def test_search_turns_on_web_search(self):
        self.maja.reply("search: news about Chalmers")
        self.assertEqual(self.requests[0]["tools"][0]["type"], "web_search_20260209")

    @unittest.skipUnless(CLOUD_AVAILABLE, "needs the anthropic package")
    def test_budget_stops_cloud_use(self):
        self.assertIn("$1.00 per month", self.maja.reply("cloud budget 1"))
        self.maja.change_setting("cloud_spend", {f"{datetime.now():%Y-%m}": 1.0})
        answer = self.maja.reply("think: anything")
        self.assertIn("budget of $1.00 is used up", answer)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.maja.reply("normal question"), "Local answer.")

    def test_plain_messages_never_use_the_cloud(self):
        self.maja.reply("What's a good breakfast?")
        self.assertEqual(self.requests, [])

    @unittest.skipUnless(CLOUD_AVAILABLE, "needs the anthropic package")
    def test_missing_key_is_explained(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}):
            self.assertIn("needs a Claude API key", self.maja.reply("think: hi"))


@unittest.skipUnless(AGENDA_AVAILABLE, "needs icalendar and recurring-ical-events")
class CalendarTests(unittest.TestCase):
    SAMPLE = str(Path(__file__).with_name("demo_documents") / "sample-calendar.ics")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        folder = Path(self.temporary.name)
        self.brain = FakeBrain("Hi!")
        self.maja = Maja(folder / "data.json", self.brain)
        self.maja.agenda = Agenda(self.maja, folder / "cache.ics")

    def tearDown(self):
        self.temporary.cleanup()

    def test_weekly_events_are_expanded(self):
        self.maja.agenda.connect(self.SAMPLE)
        events = self.maja.agenda.events(datetime(2026, 10, 19), datetime(2026, 10, 22))
        self.assertEqual([describe_event(event, with_day=True) for event in events], [
            "Mon 19 Oct, 10:00-12:00 Sample lecture: Parallel programming (Lecture hall HA2)",
            "Tue 20 Oct, 17:30-18:30 Gym",
            "Wed 21 Oct, 10:00-12:00 Sample lecture: Parallel programming (Lecture hall HA2)",
        ])
        self.assertEqual([describe_event(event) for event in self.maja.agenda.day(date(2026, 12, 18))],
                         ["all day: Sample exam: Parallel programming"])

    def test_calendar_commands(self):
        self.assertIn("No calendar is connected", self.maja.reply("calendar"))
        self.assertIn("doesn't look like a calendar link", self.maja.reply("calendar hello"))
        self.assertIn("connected", self.maja.reply(f"calendar {self.SAMPLE}"))
        self.assertEqual(self.maja.setting("calendar_url"), self.SAMPLE)
        self.assertIn("disconnected", self.maja.reply("calendar off"))
        self.assertIsNone(self.maja.setting("calendar_url"))

    def test_briefing_and_replies_know_the_calendar(self):
        self.maja.agenda.connect(self.SAMPLE)
        text = self.maja.morning_briefing(datetime(2026, 10, 19, 7, 0))
        self.assertIn("Today, Monday 19 October:\n📅 10:00-12:00 Sample lecture", text)
        self.maja.reply("When is my next lecture?")
        self.assertIn("Matty's calendar for the next 7 days", self.brain.calls[-1][0]["content"])

    def test_offline_copy_is_used_when_the_link_stops_working(self):
        self.maja.agenda.connect(self.SAMPLE)
        self.maja.change_setting("calendar_url", "https://example.invalid/calendar.ics")
        self.maja.agenda._calendar = None  # Force a new download, which fails.
        events = self.maja.agenda.events(datetime(2026, 10, 19), datetime(2026, 10, 20))
        self.assertEqual(len(events), 1)


class ScriptedBrain:
    """Answers each role of a job with a scripted reply, in order."""

    model = "fake-local"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_job(self, replies):
        brain = ScriptedBrain(replies)
        maja = Maja(self.folder / "data.json", brain)
        runner = JobRunner(maja, self.folder / "jobs")
        steps = []
        summary = runner.run("Plan my study week", steps.append, now=datetime(2026, 10, 21, 14, 0))
        return brain, runner, steps, summary

    def test_a_good_draft_goes_through_four_roles(self):
        brain, runner, steps, summary = self.run_job(
            ["The plan", "The research", "The study week draft", "VERDICT: GOOD"])
        self.assertEqual(steps, ["Planner is working...", "Researcher is working...",
                                 "Writer is working...", "Reviewer is working..."])
        self.assertIn("Plan:\nThe plan\n\nResearch:\nThe research", brain.calls[2][1]["content"])
        self.assertIn("The study week draft", summary)
        self.assertIn("The reviewer approved it.", summary)
        report = runner.last_report()
        self.assertEqual(report.name, "2026-10-21_1400_plan-my-study-week.md")
        text = report.read_text(encoding="utf-8")
        self.assertIn("## Result\n\nThe study week draft", text)
        self.assertIn("### Researcher\n\nThe research", text)

    def test_review_problems_are_fixed_once(self):
        brain, runner, steps, summary = self.run_job(
            ["Plan", "Research", "Draft 1", "VERDICT: NEEDS WORK\n1. Add Sunday",
             "Draft 2 with Sunday", "VERDICT: GOOD"])
        self.assertEqual(len(brain.calls), 6)
        self.assertIn("1. Add Sunday", brain.calls[4][1]["content"])
        self.assertIn("Draft 2 with Sunday", summary)
        self.assertIn("approved", summary)

    def test_still_not_approved_after_one_fix(self):
        brain, runner, steps, summary = self.run_job(
            ["Plan", "Research", "Draft 1", "VERDICT: NEEDS WORK", "Draft 2", "VERDICT: NEEDS WORK"])
        self.assertEqual(len(brain.calls), 6)  # It doesn't loop forever.
        self.assertIn("still had remarks", summary)


class MarkdownTests(unittest.TestCase):
    def test_common_formatting_becomes_styles(self):
        runs = markdown_runs("### Day 1\n* **Goal:** read *notes* and `MPI_Send`\n---")
        self.assertEqual(runs, [
            ("Day 1", ("heading",)), ("\n", ()),
            ("• ", ()), ("Goal:", ("bold",)), (" read ", ()), ("notes", ("italic",)),
            (" and ", ()), ("MPI_Send", ("code",)), ("\n", ()),
            ("─" * 24, ()),
        ])

    def test_normal_text_is_left_alone(self):
        text = "2 * 3 = 6 and the file my_notes_v2.md"
        self.assertEqual(markdown_runs(text), [(text, ())])
        self.assertEqual(plain_text("**Hi** Matty!\n- one\n  - two"), "Hi Matty!\n• one\n    • two")

    def test_code_blocks_keep_their_text(self):
        self.assertEqual(markdown_runs("```\nmpirun -n 4\n```"), [("mpirun -n 4\n", ("code",))])


class RepeatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.maja = Maja(Path(self.temporary.name) / "data.json", FakeBrain("Sure!"))

    def tearDown(self):
        self.temporary.cleanup()

    def test_repeat_phrases(self):
        cases = {"take vitamins every day at 8": "daily", "every morning": "daily",
                 "water the plants every Monday": "weekly", "every week": "weekly",
                 "stand-up every weekday at 9": "weekdays", "pay rent every month": "monthly",
                 "call mom on Sunday": None, "everyone is here": None}
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(guess_repeat(message), expected)

    def test_next_times(self):
        friday = datetime(2026, 10, 23, 9, 0)
        self.assertEqual(next_due(friday, "daily", friday), datetime(2026, 10, 24, 9, 0))
        self.assertEqual(next_due(friday, "weekdays", friday), datetime(2026, 10, 26, 9, 0))
        self.assertEqual(next_due(friday, "weekly", friday), datetime(2026, 10, 30, 9, 0))
        self.assertEqual(next_due(datetime(2027, 1, 31, 9, 0), "monthly", datetime(2027, 1, 31, 9, 0)),
                         datetime(2027, 2, 28, 9, 0))
        # Missed times while Maja was closed are skipped, not repeated.
        self.assertEqual(next_due(datetime(2026, 10, 1, 8, 0), "daily", datetime(2026, 10, 22, 12, 0)),
                         datetime(2026, 10, 23, 8, 0))

    def test_repeating_reminder_fires_and_moves_on(self):
        self.maja.data["tasks"] = [{"text": "Vitamins", "done": False, "due": "2026-10-22 08:00",
                                    "repeat": "daily"}]
        fired = self.maja.due_reminders(datetime(2026, 10, 22, 8, 0))
        self.assertEqual([task["text"] for task in fired], ["Vitamins"])
        task = self.maja.data["tasks"][0]
        self.assertEqual((task["due"], task["done"]), ("2026-10-23 08:00", False))
        self.assertNotIn("reminded", task)
        self.assertIn("(⏰ Fri 23 Oct, 08:00, every day)", self.maja.reply("show tasks"))

    def test_finishing_a_repeating_task_moves_it_to_the_next_time(self):
        today = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
        self.maja.data["tasks"] = [{"text": "Vitamins", "done": False,
                                    "due": today.strftime("%Y-%m-%d %H:%M"), "repeat": "daily"}]
        answer = self.maja.reply("finish task 1")
        self.assertIn("Done for now! Vitamins", answer)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(self.maja.data["tasks"][0]["due"], tomorrow)
        # Already pointing at tomorrow (today's reminder went off): finishing again changes nothing.
        self.maja.reply("finish task 1")
        self.assertEqual(self.maja.data["tasks"][0]["due"], tomorrow)

    def test_repeating_task_from_conversation(self):
        self.maja.brain = FakeBrain("Okay!", {"new_tasks": [{"text": "Take vitamins", "due": ""}]})
        answer = self.maja.reply("Remind me to take vitamins every day at 8")
        task = self.maja.data["tasks"][0]
        self.assertEqual((task["repeat"], task["due"][-5:]), ("daily", "08:00"))
        self.assertIn("every day)", answer)

    def test_schedules_and_delete_commands(self):
        first = datetime(2026, 10, 26, 8, 0)
        self.maja.add_schedule("plan my week", "weekly", first)
        self.assertIn("1. 🔁 plan my week (every week, next: Mon 26 Oct, 08:00)", self.maja.reply("schedules"))
        self.assertEqual(self.maja.due_schedules(datetime(2026, 10, 26, 7, 59)), [])
        ready = self.maja.due_schedules(datetime(2026, 10, 26, 8, 0))
        self.assertEqual([s["task"] for s in ready], ["plan my week"])
        self.assertEqual(self.maja.data["schedules"][0]["next"], "2026-11-02 08:00")
        self.assertIn("Deleted: plan my week", self.maja.reply("delete schedule 1"))
        self.maja.reply("add task Old task")
        self.assertIn("Deleted: Old task", self.maja.reply("delete task 1"))
        self.assertIn("Use a number", self.maja.reply("delete task 5"))


class PhotoTests(unittest.TestCase):
    def test_photo_questions_go_to_the_vision_model_and_the_photo_is_not_stored(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("You're holding a red mug.")
            assistant = Maja(Path(folder) / "data.json", brain)
            answer = assistant.reply_about_photo("What am I holding?", "BASE64PHOTO")
            self.assertEqual(answer, "You're holding a red mug.\n📷 Looked at one photo (not saved)")
            self.assertEqual(brain.calls[0][-1], {"photo": "BASE64PHOTO"})
            self.assertEqual(assistant.history[0]["content"], "(with a photo 📷) What am I holding?")
            self.assertNotIn("BASE64PHOTO", (Path(folder) / "data.json").read_text(encoding="utf-8"))

    def test_empty_message_asks_what_maja_sees(self):
        with tempfile.TemporaryDirectory() as folder:
            brain = FakeBrain("A desk.")
            Maja(Path(folder) / "data.json", brain).reply_about_photo("", "PHOTO")
            self.assertEqual(brain.calls[0][1]["content"], "What do you see?")

    @unittest.skipUnless(CAMERA_AVAILABLE, "needs opencv-python (requirements.txt)")
    def test_frames_are_shrunk_for_the_model_and_preview(self):
        import base64
        import numpy as np
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        jpeg = base64.b64decode(photo_for_model(frame))
        self.assertEqual(jpeg[:2], b"\xff\xd8")  # A JPEG file.
        import cv2
        decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape[:2], (432, 768))
        self.assertEqual(base64.b64decode(preview_png(frame))[:4], b"\x89PNG")


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
