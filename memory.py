"""Long-term memory: Maja remembers every conversation, not just the last 20 messages.

Each question and answer is saved twice: as a line in chat_archive.jsonl (the full record),
and as an embedding in its own ChromaDB collection, so it can be found by meaning later.
For every new message, the closest older conversations that aren't in the recent chat
anymore are given to the model, with their dates. Everything stays on this computer.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

# Cosine distance up to which an old conversation counts as related. Measured with
# nomic-embed-text on 8 stored conversations: questions about an old topic found the
# right one at 0.16-0.33, unrelated questions and small talk were 0.34-0.50.
RELATED = 0.34
STORED_CHARS = 1500  # Long answers are shortened for searching; the archive keeps them whole.


class Memory:
    def __init__(self, library, brain, archive_file):
        self.brain = brain
        self.archive_file = Path(archive_file)
        self._library = library
        self._chats = library.collection("maja-chats")
        self._lock = threading.Lock()
        # Goes up with every 'forget chats'. Work that started before (like filling the
        # memory at startup) passes the number it started with and is dropped if it changed,
        # so forgotten conversations can't come back.
        self.generation = 0

    def count(self):
        return self._chats.count()

    def remember(self, question, answer, when=None, generation=None):
        when = when or datetime.now()
        record = {"time": f"{when:%Y-%m-%d %H:%M}", "matty": question, "maja": answer}
        text = f"Matty: {question}\nMaja: {answer}"[:STORED_CHARS]
        embedding = self.brain.embed([f"search_document: {text}"])  # Slow, so outside the lock.
        with self._lock:
            if generation is not None and generation != self.generation:
                return False
            with self.archive_file.open("a", encoding="utf-8") as archive:
                archive.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._chats.add(
                ids=[f"{when:%Y%m%d%H%M%S%f}-{self._chats.count()}"],
                embeddings=embedding,
                documents=[text],
                metadatas=[{"time": record["time"], "question": question[:500]}],
            )
        return True

    def recall(self, message, skip_questions=(), results=3):
        """Older conversations related to the message, oldest first. Conversations whose
        question is still in the recent chat (skip_questions) are left out."""
        if self._chats.count() == 0:
            return []
        found = self._chats.query(query_embeddings=self.brain.embed([f"search_query: {message}"]),
                                  n_results=min(results + len(skip_questions), self._chats.count()))
        hits = [
            {"time": meta["time"], "text": text, "distance": distance}
            for text, meta, distance in zip(found["documents"][0], found["metadatas"][0], found["distances"][0])
            if distance <= RELATED and meta["question"] not in skip_questions
        ][:results]
        return sorted(hits, key=lambda hit: hit["time"])

    def forget_everything(self):
        with self._lock:
            self.generation += 1
            self._library._client.delete_collection("maja-chats")
            self._chats = self._library.collection("maja-chats")
            self.archive_file.unlink(missing_ok=True)
