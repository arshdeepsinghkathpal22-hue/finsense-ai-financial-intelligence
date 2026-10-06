"""Structure-aware chunking.

Rules, in order of precedence:

1. A heading always starts a new chunk, and the heading travels with every
   chunk of its section (stored as metadata and prepended for embedding).
2. Tables become their own chunks so rows are never split from their
   header; a long table is split by rows with the header repeated.
3. Paragraphs are packed until the target size is reached. A paragraph
   longer than the target is split on sentence boundaries. A section shorter
   than ``min_section_words`` is merged into the following section instead
   of becoming a fragment that matches every query about the document.
4. Consecutive chunks in the same section overlap by roughly
   ``overlap_words`` (whole sentences), so a fact that straddles a boundary
   is still retrievable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.services.rag.extraction import ExtractedDocument

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

INJECTION_PATTERNS = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+"
    r"(instructions|prompts|rules)|system\s+prompt|you\s+are\s+now|reveal\s+(your|the)\s+"
    r"(instructions|prompt|secrets?|api\s*keys?)|api[_\s-]?key|password",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    index: int
    text: str
    page_start: int
    page_end: int
    heading: str | None
    is_table: bool
    flags: list[str]

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _words(text: str) -> int:
    return len(text.split())


def _tail_overlap(sentences: list[str], overlap_words: int) -> list[str]:
    """Last whole sentences totalling at most ``overlap_words`` words."""
    tail: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        n = _words(sentence)
        if total + n > overlap_words:
            break
        tail.insert(0, sentence)
        total += n
    return tail


def _flags_for(text: str) -> list[str]:
    return ["instruction_like_text"] if INJECTION_PATTERNS.search(text) else []


def chunk_document(
    doc: ExtractedDocument, *, target_words: int = 180, overlap_words: int = 30, min_section_words: int = 40,
) -> list[Chunk]:
    if target_words < 40:
        raise ValueError("target_words must be at least 40")
    if not 0 <= overlap_words < target_words // 2:
        raise ValueError("overlap_words must be between 0 and half the target size")

    chunks: list[Chunk] = []
    heading: str | None = None
    sentences: list[str] = []  # sentences in the current buffer
    fresh_words = 0  # words in the buffer that did not come from overlap
    page_start = page_end = 1

    def emit(text: str, start: int, end: int, is_table: bool = False) -> None:
        chunks.append(Chunk(len(chunks), text, start, end, heading, is_table, _flags_for(text)))

    def flush(keep_overlap: bool) -> None:
        nonlocal sentences, fresh_words, page_start
        if sentences and fresh_words > 0:
            emit(" ".join(sentences), page_start, page_end)
        sentences = _tail_overlap(sentences, overlap_words) if keep_overlap and fresh_words > 0 else []
        fresh_words = 0
        page_start = page_end

    items = [(page.number, block) for page in doc.pages for block in page.blocks]
    for position, (page_number, block) in enumerate(items):
        next_kind = items[position + 1][1].kind if position + 1 < len(items) else None
        if block.kind == "heading":
            if 0 < fresh_words < min_section_words and next_kind == "paragraph":
                # A tiny section (e.g. a one-line scheme summary under the
                # title) is not useful alone: carry it into the next
                # section, keeping the new heading inline as context.
                sentences.append(block.text.rstrip(":") + ":")
                fresh_words += _words(block.text)
                heading = f"{heading} / {block.text}"[:200] if heading else block.text[:200]
                continue
            flush(keep_overlap=False)
            heading = block.text[:200]
            page_start = page_end = page_number
            continue
        if block.kind == "table":
            flush(keep_overlap=False)
            page_start = page_end = page_number
            for part in _split_table(block.text, target_words * 2):
                emit(part, page_number, page_number, is_table=True)
            continue
        # paragraph
        if not sentences:
            page_start = page_number
        page_end = page_number
        for sentence in split_sentences(block.text) or [block.text]:
            n = _words(sentence)
            if fresh_words and fresh_words + n > target_words:
                flush(keep_overlap=True)
                page_start = page_number
            if n > target_words * 1.5:
                # A single enormous "sentence" (e.g. a list without
                # punctuation): emit overlapping word windows instead.
                flush(keep_overlap=False)
                words = sentence.split()
                step = max(1, target_words - overlap_words)
                for i in range(0, len(words), step):
                    window = words[i:i + target_words]
                    sentences, fresh_words = [" ".join(window)], len(window)
                    flush(keep_overlap=False)
                    if i + target_words >= len(words):
                        break
                continue
            sentences.append(sentence)
            fresh_words += n
    flush(keep_overlap=False)
    return chunks


def _split_table(text: str, max_words: int) -> list[str]:
    rows = text.split("\n")
    if _words(text) <= max_words or len(rows) < 3:
        return [text]
    header, body = rows[0], rows[1:]
    parts, current = [], [header]
    for row in body:
        if _words(" ".join(current)) + _words(row) > max_words and len(current) > 1:
            parts.append("\n".join(current))
            current = [header]
        current.append(row)
    if len(current) > 1:
        parts.append("\n".join(current))
    return parts


def embedding_text(context_label: str, chunk: Chunk) -> str:
    """Text actually embedded: document label and section give the vector context.

    Table cell separators are replaced by plain punctuation so they do not
    contribute tokens to the (bag-of-tokens) embedding.
    """
    parts = [context_label]
    if chunk.heading:
        parts.append(chunk.heading)
    body = chunk.text.replace(" | ", "; ") if chunk.is_table else chunk.text
    return ". ".join(parts) + "\n" + body
