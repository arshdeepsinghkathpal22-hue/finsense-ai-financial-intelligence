"""Page-aware, structure-aware text extraction for PDF and plain-text documents.

Output is a list of pages, each a sequence of blocks (heading, paragraph or
table) in reading order. This module deliberately imports nothing from the
rest of the application so it can run as a short-lived, resource-limited
worker process (``python -m app.services.rag.extraction``) with a hard
timeout (see :func:`extract_with_timeout`).
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

PARSER_VERSION = "2026.10-1"


class ExtractionError(Exception):
    """The document could not be parsed (corrupt, encrypted, too large...)."""


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "table"
    text: str


@dataclass
class Page:
    number: int
    blocks: list[Block] = field(default_factory=list)
    image_only: bool = False


@dataclass
class ExtractedDocument:
    pages: list[Page]
    page_count: int
    needs_ocr: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> ExtractedDocument:
        pages = [Page(p["number"], [Block(**b) for b in p["blocks"]], p["image_only"]) for p in data["pages"]]
        return ExtractedDocument(pages, data["page_count"], data["needs_ocr"], data.get("warnings", []))


_WS = re.compile(r"[ \t ]+")
_PAGE_LABEL = re.compile(r"^(page\s*)?\d+(\s*(of|/)\s*\d+)?$", re.IGNORECASE)


def clean_text(text: str) -> str:
    text = text.replace("­", "")  # soft hyphens
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    return _WS.sub(" ", text).strip()


def join_lines(lines: list[str]) -> str:
    """Joins wrapped lines; a trailing hyphen before a lowercase word is kept as a compound."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-") and line[:1].islower():
            out += line
        elif out:
            out += " " + line
        else:
            out = line
    return clean_text(out)


# --------------------------------------------------------------------------- PDF


def _inside(obj: dict, bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return x0 - 1 <= cx <= x1 + 1 and top - 1 <= cy <= bottom + 1


def _render_table(rows: list[list[str | None]]) -> str:
    cleaned = [[clean_text(cell or "") for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(row)]
    return "\n".join(" | ".join(row) for row in cleaned)


def extract_pdf(path: str, max_pages: int) -> dict:
    import pdfplumber  # imported here: only needed inside the worker

    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:  # pdfminer raises many different exception types
        raise ExtractionError("The PDF could not be opened (it may be corrupt or encrypted).") from exc
    with pdf:
        if len(pdf.pages) > max_pages:
            raise ExtractionError(f"The PDF has {len(pdf.pages)} pages; the limit is {max_pages}.")
        if len(pdf.pages) == 0:
            raise ExtractionError("The PDF has no pages.")
        sizes: list[float] = []
        for page in pdf.pages[: min(len(pdf.pages), 20)]:
            sizes += [round(c["size"], 1) for c in page.chars]
        body_size = statistics.mode(sizes) if sizes else 10.0

        pages: list[Page] = []
        for number, page in enumerate(pdf.pages, start=1):
            if not page.chars:
                pages.append(Page(number, [], image_only=bool(page.images)))
                continue
            tables = page.find_tables()
            items: list[tuple[float, Block]] = []
            for table in tables:
                rendered = _render_table(table.extract())
                if rendered:
                    items.append((table.bbox[1], Block("table", rendered)))
            boxes = [t.bbox for t in tables]
            text_page = (page.filter(lambda obj, boxes=boxes: not any(_inside(obj, b) for b in boxes))
                         if boxes else page)
            lines = text_page.extract_text_lines(return_chars=True, strip=True)
            items += _group_lines(lines, body_size)
            items.sort(key=lambda item: item[0])
            pages.append(Page(number, [block for _, block in items]))

    _remove_repeated_lines(pages)
    needs_ocr = _looks_scanned(pages)
    warnings = []
    if needs_ocr:
        warnings.append("Most pages contain only images; OCR is required to extract text.")
    return ExtractedDocument(pages, len(pages), needs_ocr, warnings).to_dict()


def _group_lines(lines: list[dict], body_size: float) -> list[tuple[float, Block]]:
    """Groups text lines into headings and paragraphs using font size, weight and spacing."""
    out: list[tuple[float, Block]] = []
    buffer: list[str] = []
    buffer_top = 0.0
    previous_bottom: float | None = None

    def flush() -> None:
        nonlocal buffer
        if buffer:
            out.append((buffer_top, Block("paragraph", join_lines(buffer))))
            buffer = []

    for line in lines:
        text = clean_text(line["text"])
        if not text:
            continue
        chars = line.get("chars") or []
        size = statistics.mean(c["size"] for c in chars) if chars else body_size
        bold = bool(chars) and all("bold" in c.get("fontname", "").lower() for c in chars if c["text"].strip())
        is_heading = (size >= body_size * 1.18 or bold) and len(text) <= 120 and not text.endswith(".")
        if is_heading:
            flush()
            # Consecutive heading lines (a wrapped title) are merged.
            if out and out[-1][1].kind == "heading" and previous_bottom is not None \
                    and line["top"] - previous_bottom < size * 0.8:
                out[-1][1].text = clean_text(out[-1][1].text + " " + text)
            else:
                out.append((line["top"], Block("heading", text)))
            previous_bottom = line["bottom"]
            continue
        gap = line["top"] - previous_bottom if previous_bottom is not None else 0.0
        if buffer and gap > size * 0.55:
            flush()
        if not buffer:
            buffer_top = line["top"]
        buffer.append(text)
        previous_bottom = line["bottom"]
    flush()
    return out


def _normalise_for_repeat(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _remove_repeated_lines(pages: list[Page]) -> None:
    """Drops running headers/footers: short lines repeated on most pages."""
    text_pages = [p for p in pages if p.blocks]
    if len(text_pages) < 2:
        for page in pages:
            page.blocks = [b for b in page.blocks if not _PAGE_LABEL.match(b.text)]
        return
    counts: Counter[str] = Counter()
    for page in text_pages:
        seen = set()
        for block in page.blocks:
            if block.kind != "table" and len(block.text) < 160:
                seen.add(_normalise_for_repeat(block.text))
        counts.update(seen)
    threshold = max(2, int(len(text_pages) * 0.6))
    boilerplate = {text for text, count in counts.items() if count >= threshold}
    for page in pages:
        page.blocks = [
            b for b in page.blocks
            if _normalise_for_repeat(b.text) not in boilerplate and not _PAGE_LABEL.match(b.text)
        ]


def _looks_scanned(pages: list[Page]) -> bool:
    image_only = sum(1 for p in pages if p.image_only)
    text_pages = sum(1 for p in pages if p.blocks)
    return image_only > 0 and image_only >= text_pages


# --------------------------------------------------------------------------- plain text

_MD_HEADING = re.compile(r"^#{1,6}\s+(.+)$")


def _is_text_heading(line: str, next_line: str | None) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    if _MD_HEADING.match(line):
        return True
    if next_line is not None and re.fullmatch(r"[=\-]{3,}", next_line.strip()):
        return True
    return bool(letters) and len(line) <= 80 and all(ch.isupper() for ch in letters) and len(letters) >= 3


def extract_text(path: str) -> dict:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionError("Text documents must be UTF-8 encoded.") from exc
    raw_pages = text.split("\f")
    pages = []
    for number, page_text in enumerate(raw_pages, start=1):
        lines = page_text.splitlines()
        blocks: list[Block] = []
        paragraph: list[str] = []

        def flush(paragraph: list[str] = paragraph, blocks: list[Block] = blocks) -> None:
            if paragraph:
                blocks.append(Block("paragraph", join_lines(paragraph)))
                paragraph.clear()

        skip_next = False
        for i, line in enumerate(lines):
            if skip_next:
                skip_next = False
                continue
            stripped = line.strip()
            if not stripped:
                flush()
                continue
            next_line = lines[i + 1] if i + 1 < len(lines) else None
            if _is_text_heading(stripped, next_line):
                flush()
                match = _MD_HEADING.match(stripped)
                blocks.append(Block("heading", clean_text(match.group(1) if match else stripped)))
                skip_next = next_line is not None and bool(re.fullmatch(r"[=\-]{3,}", next_line.strip()))
                continue
            paragraph.append(stripped)
        flush()
        pages.append(Page(number, blocks))
    _remove_repeated_lines(pages)
    if not any(p.blocks for p in pages):
        raise ExtractionError("The text document is empty.")
    return ExtractedDocument(pages, len(pages), False).to_dict()


# --------------------------------------------------------------------------- isolation

WORKER_MEMORY_LIMIT_BYTES = 1536 * 1024 * 1024


def _run(path: str, mime_type: str, max_pages: int) -> dict:
    if mime_type == "application/pdf":
        return extract_pdf(path, max_pages)
    return extract_text(path)


def _limit_resources(timeout_s: int) -> None:  # pragma: no cover - runs in the child
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEMORY_LIMIT_BYTES, WORKER_MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (timeout_s + 5, timeout_s + 5))


def extract_with_timeout(path: Path, mime_type: str, *, max_pages: int, timeout_s: int) -> ExtractedDocument:
    """Parses a document in a separate, resource-limited process.

    A hostile or pathological file can therefore neither hang the API (wall
    clock timeout), exhaust its memory (address-space limit on POSIX), nor
    crash it (the worker dies instead).
    """
    command = [sys.executable, "-m", "app.services.rag.extraction", str(path), mime_type, str(max_pages),
               str(timeout_s)]
    try:
        # Fixed argument list (no shell); the path is a server-generated storage path.
        completed = subprocess.run(  # noqa: S603
            command, capture_output=True, timeout=timeout_s, check=False,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"Parsing took longer than {timeout_s} seconds and was stopped.") from exc
    try:
        payload = json.loads(completed.stdout.decode("utf-8") or "{}")
    except ValueError:
        payload = {}
    if completed.returncode != 0 or "error" in payload or "result" not in payload:
        message = payload.get("error") or "The parser process failed while reading this file."
        raise ExtractionError(message)
    return ExtractedDocument.from_dict(payload["result"])


def _worker_main(argv: list[str]) -> int:  # pragma: no cover - exercised via subprocess
    path, mime_type, max_pages, timeout_s = argv[0], argv[1], int(argv[2]), int(argv[3])
    if os.name == "posix":
        # Limits are applied by the worker itself (not via preexec_fn, which
        # is unsafe in a multi-threaded parent such as the API server).
        _limit_resources(timeout_s)
    try:
        result = _run(path, mime_type, max_pages)
    except ExtractionError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    except MemoryError:
        print(json.dumps({"error": "The document needs more memory to parse than is allowed."}))
        return 1
    except Exception:
        print(json.dumps({"error": "The document could not be parsed."}))
        return 1
    print(json.dumps({"result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main(sys.argv[1:]))
