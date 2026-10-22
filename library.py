"""Maja's library: answers from Matty's own documents (RAG, retrieval-augmented generation).

How it works:
1. Read every PDF, .txt and .md file in the documents folder.
2. Split the text into chunks of about a paragraph or two.
3. Turn each chunk into an embedding (a list of numbers that captures its meaning)
   with a local Ollama model, and store it in a ChromaDB vector database.
4. For a question, find the chunks with the closest meaning and give them to the
   AI model, which answers from them and names its sources.

Everything stays on this computer. Needs the packages in requirements.txt.
"""

import json
from pathlib import Path

try:
    import chromadb
    from pypdf import PdfReader
    LIBRARY_AVAILABLE = True
except ImportError:
    LIBRARY_AVAILABLE = False

SUPPORTED = {".pdf", ".txt", ".md"}
CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
EMBED_BATCH = 32
# How close a chunk's meaning must be to the question (cosine distance: 0 = same
# meaning, 1 = unrelated). Measured with nomic-embed-text: real questions about the
# documents scored 0.16–0.43, ordinary chat 0.41–0.55. So chunks up to MAX_DISTANCE are
# given to the model (it's told to ignore unhelpful ones), but a source is only shown
# as used when it's a CLEAR_MATCH or the answer names it.
MAX_DISTANCE = 0.45
CLEAR_MATCH = 0.40


class LibraryError(Exception):
    """A friendly, user-facing explanation of a problem with the documents."""


def read_document(path):
    """Return [(page number or None, text), ...] for a supported file."""
    if path.suffix.lower() == ".pdf":
        try:
            reader = PdfReader(path)
            return [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, 1)]
        except Exception as error:  # Broken or protected PDFs fail in many ways.
            raise LibraryError(f"I couldn't read {path.name}: it may be damaged or password-protected.") from error
    return [(None, path.read_text(encoding="utf-8", errors="replace"))]


def split_into_chunks(text):
    """Split text into pieces of about CHUNK_CHARS, keeping paragraphs together where possible."""
    chunks, current = [], ""
    for paragraph in (p.strip() for p in text.split("\n\n")):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 1 > CHUNK_CHARS:
            chunks.append(current)
            current = ""
        while len(paragraph) > CHUNK_CHARS:  # A very long paragraph: cut it, with overlap.
            cut = paragraph.rfind(" ", 0, CHUNK_CHARS)
            cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
            chunks.append(paragraph[:cut])
            paragraph = paragraph[max(cut - OVERLAP_CHARS, 1):]
        current = f"{current} {paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def source_label(source, page):
    return f"{source} p. {page}" if page else source


class Library:
    def __init__(self, folder, database_folder, brain):
        if not LIBRARY_AVAILABLE:
            raise LibraryError("Reading documents needs extra packages. In a terminal, run: "
                               "py -m pip install -r requirements.txt, then restart me.")
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.brain = brain
        database_folder = Path(database_folder)
        database_folder.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(database_folder))
        self._chunks = self._client.get_or_create_collection(
            "maja-documents", metadata={"hnsw:space": "cosine"})
        # Remembers each file's size and change time, so unchanged files aren't re-read.
        self._index_file = database_folder / "files.json"
        self._files = json.loads(self._index_file.read_text(encoding="utf-8")) if self._index_file.exists() else {}

    def collection(self, name):
        """Another collection in the same database (used for the chat memory)."""
        return self._client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    def documents(self):
        return sorted(self._files)

    def _scan(self):
        return {
            path.relative_to(self.folder).as_posix(): path
            for path in self.folder.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED and not path.name.startswith("~$")
        }

    def update(self):
        """Read new and changed documents, forget deleted ones.

        Returns {'added': [...], 'updated': [...], 'removed': [...], 'problems': [...]}.
        """
        report = {"added": [], "updated": [], "removed": [], "problems": []}
        found = self._scan()
        for source in [s for s in self._files if s not in found]:
            self._chunks.delete(where={"source": source})
            del self._files[source]
            report["removed"].append(source)
        for source, path in found.items():
            stat = path.stat()
            fingerprint = [stat.st_size, stat.st_mtime]
            if self._files.get(source) == fingerprint:
                continue
            try:
                self._add(source, path)
            except LibraryError as error:
                report["problems"].append(str(error))
                continue
            report["updated" if source in self._files else "added"].append(source)
            self._files[source] = fingerprint
        self._index_file.write_text(json.dumps(self._files, indent=2), encoding="utf-8")
        return report

    def _add(self, source, path):
        pieces = [(page, chunk) for page, text in read_document(path) for chunk in split_into_chunks(text)]
        self._chunks.delete(where={"source": source})
        for start in range(0, len(pieces), EMBED_BATCH):
            batch = pieces[start:start + EMBED_BATCH]
            embeddings = self.brain.embed([f"search_document: {chunk}" for _, chunk in batch])
            self._chunks.add(
                ids=[f"{source}#{start + i}" for i in range(len(batch))],
                embeddings=embeddings,
                documents=[chunk for _, chunk in batch],
                metadatas=[{"source": source, "page": page or 0} for page, _ in batch],
            )

    def search(self, question, results=4):
        """Return the chunks closest in meaning to the question, closest first.

        Each hit is {'source', 'page', 'text', 'distance'}. Chunks that aren't close
        enough are left out, so ordinary chat doesn't pull in random documents.
        """
        if not self._files:
            return []
        query = self.brain.embed([f"search_query: {question}"])
        found = self._chunks.query(query_embeddings=query, n_results=results)
        return [
            {"source": meta["source"], "page": meta["page"] or None, "text": text, "distance": distance}
            for text, meta, distance in zip(found["documents"][0], found["metadatas"][0], found["distances"][0])
            if distance <= MAX_DISTANCE
        ]
