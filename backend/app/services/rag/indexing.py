"""Document upload validation, storage and indexing (extract -> chunk -> embed -> store)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import (
    AppError,
    ConflictError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.db import get_sessionmaker
from app.models import Document, DocumentChunk
from app.services.rag import chunking, extraction
from app.services.rag.embeddings import get_embedder

logger = logging.getLogger("finsense.indexing")

ALLOWED_EXTENSIONS = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}
DOC_TYPES = ("factsheet", "annual_report", "sid", "research", "other")
EMBED_BATCH = 64


def current_index_config(settings: Settings) -> dict:
    embedder = get_embedder()
    return {
        "embedding_model": embedder.name,
        "embedding_dim": embedder.dim,
        "chunk_target_words": settings.chunk_target_words,
        "chunk_overlap_words": settings.chunk_overlap_words,
        "parser_version": extraction.PARSER_VERSION,
    }


def safe_display_filename(filename: str) -> str:
    """Filename for display only; never used as a filesystem path."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip() or "document"
    return name[:255]


def sniff_mime_type(filename: str, content: bytes) -> str:
    """Validates the declared type against the file's actual bytes."""
    extension = Path(safe_display_filename(filename)).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationFailedError(
            "Unsupported file type. Upload a PDF (.pdf) or UTF-8 text (.txt, .md) document.",
            {"allowed": sorted(ALLOWED_EXTENSIONS)},
        )
    if not content:
        raise ValidationFailedError("The uploaded file is empty.")
    if extension == ".pdf":
        if not content[:1024].lstrip().startswith(b"%PDF-"):
            raise ValidationFailedError("The file does not have a valid PDF signature.")
        if b"%%EOF" not in content[-2048:]:
            raise ValidationFailedError("The PDF appears to be truncated (no end-of-file marker).")
        return "application/pdf"
    if b"\x00" in content or content.startswith(b"%PDF-"):
        raise ValidationFailedError("The text file contains binary data.")
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailedError("Text documents must be UTF-8 encoded.") from exc
    return ALLOWED_EXTENSIONS[extension]


def store_file(settings: Settings, content: bytes, mime_type: str) -> str:
    """Writes content under a random key; returns the key."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    extension = {"application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md"}[mime_type]
    key = f"{uuid.uuid4().hex}{extension}"
    path = storage_path(settings, key)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    return key


def storage_path(settings: Settings, key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.(pdf|txt|md)", key):
        raise ValidationFailedError("Invalid storage key.")
    base = settings.upload_dir.resolve()
    path = (base / key).resolve()
    if path.parent != base:  # defence in depth against path traversal
        raise ValidationFailedError("Invalid storage key.")
    return path


def create_document(
    db: Session, settings: Settings, *, content: bytes, filename: str, owner_id: uuid.UUID | None,
    visibility: str, title: str | None, doc_type: str, fund_id: int | None, as_of_date: date | None,
    is_synthetic: bool = False,
) -> Document:
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise PayloadTooLargeError(f"Documents are limited to {settings.max_upload_mb} MB.")
    if doc_type not in DOC_TYPES:
        raise ValidationFailedError(f"doc_type must be one of {', '.join(DOC_TYPES)}.")
    if visibility not in ("private", "shared"):
        raise ValidationFailedError("visibility must be 'private' or 'shared'.")
    mime_type = sniff_mime_type(filename, content)
    digest = hashlib.sha256(content).hexdigest()
    owner_clause = Document.owner_id.is_(None) if owner_id is None else Document.owner_id == owner_id
    existing = db.scalar(select(Document).where(owner_clause, Document.sha256 == digest))
    if existing is not None:
        raise ConflictError("This document has already been uploaded.", {"document_id": str(existing.id)})
    display_name = safe_display_filename(filename)
    key = store_file(settings, content, mime_type)
    document = Document(
        owner_id=owner_id, visibility=visibility, title=(title or Path(display_name).stem)[:200],
        original_filename=display_name, storage_key=key, mime_type=mime_type, size_bytes=len(content),
        sha256=digest, doc_type=doc_type, fund_id=fund_id, as_of_date=as_of_date, is_synthetic=is_synthetic,
        status="pending", index_config={},
    )
    db.add(document)
    db.commit()
    return document


def context_label(document: Document) -> str:
    parts = [document.title]
    if document.fund is not None:
        if document.fund.name.lower() not in document.title.lower():
            parts.append(document.fund.name)
        parts.append(document.fund.scheme_code)
    return " | ".join(parts)[:400]


def index_document(db: Session, settings: Settings, document: Document) -> Document:
    """Runs the full indexing pipeline. Failures are recorded on the document."""
    document.status = "processing"
    document.error_message = None
    db.commit()
    try:
        extracted = extraction.extract_with_timeout(
            storage_path(settings, document.storage_key), document.mime_type,
            max_pages=settings.max_pdf_pages, timeout_s=settings.parser_timeout_s,
        )
        document.page_count = extracted.page_count
        if extracted.needs_ocr:
            _replace_chunks(db, document, [])
            document.status = "needs_ocr"
            document.error_message = (
                "This PDF appears to be scanned (pages are images without a text layer). "
                "Run OCR (e.g. ocrmypdf) and upload the searchable version."
            )
            db.commit()
            return document
        chunks = chunking.chunk_document(
            extracted, target_words=settings.chunk_target_words, overlap_words=settings.chunk_overlap_words
        )
        if not chunks:
            raise extraction.ExtractionError("No text could be extracted from this document.")
        embedder = get_embedder()
        label = context_label(document)
        texts = [chunking.embedding_text(label, c) for c in chunks]
        vectors = []
        for start in range(0, len(texts), EMBED_BATCH):
            vectors.extend(embedder.embed(texts[start:start + EMBED_BATCH]))
        if len(vectors) != len(chunks):
            raise ServiceUnavailableError("The embedding model returned the wrong number of vectors.")
        rows = [
            DocumentChunk(
                document_id=document.id, context_label=label, chunk_index=c.index,
                page_start=c.page_start, page_end=c.page_end,
                section_heading=c.heading, content=c.text, content_hash=c.content_hash,
                word_count=c.word_count, is_table=c.is_table, flags=c.flags,
                embedding=vector.tolist(), embedding_model=embedder.name,
            )
            for c, vector in zip(chunks, vectors, strict=True)
        ]
        _replace_chunks(db, document, rows)
        document.status = "indexed"
        document.chunk_count = len(rows)
        document.index_config = current_index_config(settings)
        document.indexed_at = datetime.now(UTC)
        db.commit()
    except extraction.ExtractionError as exc:
        db.rollback()
        _mark_failed(db, document, str(exc))
    except AppError as exc:
        db.rollback()
        _mark_failed(db, document, exc.message)
    except Exception:
        db.rollback()
        logger.exception("Indexing failed for document %s", document.id)
        _mark_failed(db, document, "Indexing failed because of an internal error.")
    return document


def _replace_chunks(db: Session, document: Document, rows: list[DocumentChunk]) -> None:
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    db.add_all(rows)
    document.chunk_count = len(rows)


def _mark_failed(db: Session, document: Document, message: str) -> None:
    document = db.merge(document)
    document.status = "failed"
    document.error_message = message[:500]
    document.chunk_count = 0
    db.commit()


def run_indexing_job(document_id: uuid.UUID, settings: Settings) -> None:
    """Background-task entry point with its own database session."""
    db = get_sessionmaker()()
    try:
        document = db.get(Document, document_id)
        if document is not None:
            index_document(db, settings, document)
    finally:
        db.close()


def delete_document(db: Session, settings: Settings, document: Document) -> None:
    path = storage_path(settings, document.storage_key)
    db.delete(document)
    db.commit()
    try:
        path.unlink()
    except FileNotFoundError:
        logger.warning("Stored file for document %s was already missing", document.id)


def needs_reindex(settings: Settings, document: Document) -> bool:
    return document.status == "indexed" and document.index_config != current_index_config(settings)
