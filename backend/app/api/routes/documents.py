"""Document library: upload, status, passages, search, reindex, delete."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentUser, DbSession, client_ip, user_rate_limit
from app.core.errors import NotFoundError, PayloadTooLargeError, PermissionDeniedError
from app.models import Document, DocumentChunk, User
from app.services import audit
from app.services.analytics import series
from app.services.fund_mentions import resolve_fund_mentions
from app.services.rag import indexing
from app.services.rag import query as query_processing
from app.services.rag.retrieval import Filters, retrieve

router = APIRouter(prefix="/documents", tags=["documents"])


def can_read(document: Document, user: User) -> bool:
    return document.visibility == "shared" or document.owner_id == user.id


def can_manage(document: Document, user: User) -> bool:
    if document.owner_id is not None:
        return document.owner_id == user.id
    return user.role == "admin"


def _readable(db: Session, user: User, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    # Unreadable and non-existent documents look identical to the caller.
    if document is None or not can_read(document, user):
        raise NotFoundError("Document not found.")
    return document


def _out(document: Document, user: User) -> dict:
    return {
        "id": str(document.id), "title": document.title, "filename": document.original_filename,
        "mime_type": document.mime_type, "size_bytes": document.size_bytes, "doc_type": document.doc_type,
        "visibility": document.visibility, "is_owner": document.owner_id == user.id,
        "can_manage": can_manage(document, user), "is_synthetic": document.is_synthetic,
        "fund": {"id": document.fund.id, "scheme_code": document.fund.scheme_code, "name": document.fund.name}
        if document.fund else None,
        "as_of_date": document.as_of_date.isoformat() if document.as_of_date else None,
        "status": document.status, "error_message": document.error_message, "page_count": document.page_count,
        "chunk_count": document.chunk_count, "index_config": document.index_config,
        "indexed_at": document.indexed_at.isoformat() if document.indexed_at else None,
        "created_at": document.created_at.isoformat(),
    }


@router.get("")
def list_documents(
    db: DbSession, user: CurrentUser,
    status_filter: Annotated[Literal["pending", "processing", "indexed", "failed", "needs_ocr"] | None,
                             Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    query = select(Document).where(or_(Document.visibility == "shared", Document.owner_id == user.id))
    if status_filter:
        query = query.where(Document.status == status_filter)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    docs = db.scalars(query.order_by(Document.created_at.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).all()
    return {"items": [_out(d, user) for d in docs], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request, background: BackgroundTasks, db: DbSession, settings: AppSettings,
    user: Annotated[User, Depends(user_rate_limit("upload"))],
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[Literal["factsheet", "annual_report", "sid", "research", "other"], Form()] = "other",
    title: Annotated[str | None, Form(max_length=200)] = None,
    fund_id: Annotated[int | None, Form()] = None,
    as_of_date: Annotated[date | None, Form()] = None,
    visibility: Annotated[Literal["private", "shared"], Form()] = "private",
) -> dict:
    if visibility == "shared" and user.role != "admin":
        raise PermissionDeniedError("Only administrators can add documents to the shared library.")
    limit = settings.max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise PayloadTooLargeError(f"Documents are limited to {settings.max_upload_mb} MB.")
    if fund_id is not None:
        series.get_fund(db, fund_id)
    document = indexing.create_document(
        db, settings, content=content, filename=file.filename or "document", title=title,
        owner_id=None if visibility == "shared" else user.id, visibility=visibility, doc_type=doc_type,
        fund_id=fund_id, as_of_date=as_of_date,
    )
    audit.record(db, "document.uploaded", user_id=user.id, ip_address=client_ip(request),
                 details={"document_id": str(document.id), "size": len(content), "visibility": visibility})
    background.add_task(indexing.run_indexing_job, document.id, settings)
    return _out(document, user)


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    return _out(_readable(db, user, document_id), user)


@router.get("/{document_id}/chunks")
def document_chunks(
    document_id: uuid.UUID, db: DbSession, user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    document = _readable(db, user, document_id)
    total = db.scalar(select(func.count()).where(DocumentChunk.document_id == document.id)) or 0
    rows = db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)
                      .order_by(DocumentChunk.chunk_index).offset((page - 1) * page_size).limit(page_size)).all()
    return {"document_id": str(document.id), "total": total, "page": page, "page_size": page_size,
            "items": [{"id": str(c.id), "index": c.chunk_index, "page_start": c.page_start, "page_end": c.page_end,
                       "section": c.section_heading, "content": c.content, "is_table": c.is_table,
                       "word_count": c.word_count, "flags": c.flags} for c in rows]}


@router.get("/{document_id}/chunks/{chunk_id}")
def document_chunk(document_id: uuid.UUID, chunk_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    document = _readable(db, user, document_id)
    chunk = db.get(DocumentChunk, chunk_id)
    if chunk is None or chunk.document_id != document.id:
        raise NotFoundError("Passage not found.")
    return {"id": str(chunk.id), "document": _out(document, user), "page_start": chunk.page_start,
            "page_end": chunk.page_end, "section": chunk.section_heading, "content": chunk.content,
            "is_table": chunk.is_table, "flags": chunk.flags}


@router.get("/{document_id}/file")
def download_document(document_id: uuid.UUID, db: DbSession, settings: AppSettings, user: CurrentUser) -> Response:
    document = _readable(db, user, document_id)
    path = indexing.storage_path(settings, document.storage_key)
    if not path.exists():
        raise NotFoundError("The stored file is missing.")
    return FileResponse(path, media_type=document.mime_type, filename=document.original_filename,
                        content_disposition_type="attachment")


@router.post("/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
def reindex_document(document_id: uuid.UUID, background: BackgroundTasks, db: DbSession, settings: AppSettings,
                     user: Annotated[User, Depends(user_rate_limit("upload"))]) -> dict:
    document = _readable(db, user, document_id)
    if not can_manage(document, user):
        raise PermissionDeniedError("You cannot re-index this document.")
    document.status = "pending"
    db.commit()
    background.add_task(indexing.run_indexing_job, document.id, settings)
    return _out(document, user)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: uuid.UUID, request: Request, db: DbSession, settings: AppSettings,
                    user: CurrentUser) -> Response:
    document = _readable(db, user, document_id)
    if not can_manage(document, user):
        raise PermissionDeniedError("You cannot delete this document.")
    indexing.delete_document(db, settings, document)
    audit.record(db, "document.deleted", user_id=user.id, ip_address=client_ip(request),
                 details={"document_id": str(document_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    doc_types: list[Literal["factsheet", "annual_report", "sid", "research", "other"]] | None = None
    top_k: int = Field(default=8, ge=1, le=20)


@router.post("/search")
def search_documents(body: SearchRequest, db: DbSession, settings: AppSettings,
                     user: Annotated[User, Depends(user_rate_limit("search"))]) -> dict:
    processed = query_processing.process(body.query)
    mentioned = [f.id for f in resolve_fund_mentions(db, processed.original)]
    result = retrieve(db, settings, processed, Filters(user.id, document_ids=body.document_ids,
                                                       doc_types=body.doc_types),
                      top_k=body.top_k, mentioned_fund_ids=mentioned)
    return {
        "query": processed.original,
        "results": [{
            "chunk_id": c.chunk_id, "document_id": c.document_id, "document_title": c.document_title,
            "doc_type": c.doc_type, "as_of_date": c.as_of_date, "page_start": c.page_start, "page_end": c.page_end,
            "section": c.section_heading, "content": c.content, "is_table": c.is_table,
            "supported": c.supported, "score": round(c.final_score, 5),
        } for c in result.candidates[: body.top_k]],
        "evidence_found": result.has_evidence,
    }
