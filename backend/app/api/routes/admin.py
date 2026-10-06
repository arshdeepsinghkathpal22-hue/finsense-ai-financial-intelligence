"""Administrator endpoints: data imports, data status, retrieval diagnostics, audit log."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AdminUser, AppSettings, DbSession, client_ip, enforce_rate_limit
from app.core.errors import NotFoundError, PayloadTooLargeError, ValidationFailedError
from app.models import (
    AuditEvent,
    DataSource,
    Document,
    Fund,
    ImportBatch,
    NavObservation,
    RagEvaluationRun,
    User,
)
from app.services import audit
from app.services.analytics import series
from app.services.fund_mentions import resolve_fund_mentions
from app.services.ingestion import amfi, csv_import
from app.services.rag import evaluation, generation, indexing
from app.services.rag import query as query_processing
from app.services.rag.retrieval import Filters, retrieve

router = APIRouter(prefix="/admin", tags=["admin"])

ImportKind = Literal["benchmarks", "benchmark_values", "funds", "nav", "aum", "sip_flows", "holdings"]


@router.post("/imports/{kind}")
async def import_csv(
    kind: ImportKind, request: Request, db: DbSession, settings: AppSettings, admin: AdminUser,
    file: Annotated[UploadFile, File()],
    source_code: Annotated[Literal["csv-import", "synthetic-demo"], Form()] = "csv-import",
) -> dict:
    limit = settings.max_csv_mb * 1024 * 1024
    filename = (file.filename or "upload.csv").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(".csv"):
        raise ValidationFailedError("Upload a .csv file.")
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise PayloadTooLargeError(f"CSV files are limited to {settings.max_csv_mb} MB.")
    csv_import.ensure_default_sources(db)
    batch = csv_import.import_csv(db, kind=kind, filename=filename, content=content, source_code=source_code,
                                  uploaded_by=admin.id, max_bytes=limit)
    audit.record(db, "data.imported", user_id=admin.id, ip_address=client_ip(request),
                 details={"kind": kind, "batch_id": str(batch.id), "status": batch.status})
    return csv_import.batch_summary(batch)


@router.get("/imports")
def list_imports(db: DbSession, _: AdminUser, page: Annotated[int, Query(ge=1)] = 1,
                 page_size: Annotated[int, Query(ge=1, le=100)] = 25) -> dict:
    total = db.scalar(select(func.count()).select_from(ImportBatch)) or 0
    rows = db.scalars(select(ImportBatch).order_by(ImportBatch.started_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for batch in rows:
        summary = csv_import.batch_summary(batch)
        summary.pop("rejected_rows")
        items.append(summary)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/imports/{batch_id}")
def get_import(batch_id: uuid.UUID, db: DbSession, _: AdminUser) -> dict:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise NotFoundError("Import batch not found.")
    return csv_import.batch_summary(batch)


@router.get("/data-status")
def data_status(db: DbSession, settings: AppSettings, _: AdminUser) -> dict:
    latest = dict(db.execute(select(NavObservation.fund_id, func.max(NavObservation.obs_date))
                             .group_by(NavObservation.fund_id)).all())
    counts = dict(db.execute(select(NavObservation.fund_id, func.count()).group_by(NavObservation.fund_id)).all())
    funds = db.scalars(select(Fund).order_by(Fund.scheme_code)).all()
    docs = db.execute(select(Document.status, func.count()).group_by(Document.status)).all()
    outdated = [d for d in db.scalars(select(Document).where(Document.status == "indexed"))
                if indexing.needs_reindex(settings, d)]
    return {
        "sources": [{"code": s.code, "name": s.name, "kind": s.kind, "is_synthetic": s.is_synthetic,
                     "description": s.description} for s in db.scalars(select(DataSource))],
        "funds": [{"id": f.id, "scheme_code": f.scheme_code, "name": f.name, "is_synthetic": f.is_synthetic,
                   "source": f.source.code, "observations": counts.get(f.id, 0),
                   "freshness": series.freshness(latest.get(f.id), settings.stale_after_days).as_dict()}
                  for f in funds],
        "documents": {status: n for status, n in docs},
        "documents_needing_reindex": len(outdated),
        "users": db.scalar(select(func.count()).select_from(User)),
    }


@router.post("/documents/reindex-outdated")
def reindex_outdated(db: DbSession, settings: AppSettings, admin: AdminUser) -> dict:
    enforce_rate_limit(f"reindex:{admin.id}", 2, 60)
    documents = [d for d in db.scalars(select(Document)) if d.status in ("failed", "pending")
                 or indexing.needs_reindex(settings, d)]
    results = []
    for document in documents:
        indexing.index_document(db, settings, document)
        results.append({"id": str(document.id), "title": document.title, "status": document.status})
    return {"reindexed": results}


class DiagnosticsRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    previous_questions: list[str] = Field(default_factory=list, max_length=5)
    mode: Literal["hybrid", "vector", "lexical"] = "hybrid"


@router.post("/rag/diagnostics")
def rag_diagnostics(body: DiagnosticsRequest, db: DbSession, settings: AppSettings, admin: AdminUser) -> dict:
    """Full retrieval trace for the administrator's own document access.

    Includes scores from every stage and the exact prompt that would be sent
    to the language model. It runs with the admin's permissions, so it never
    exposes other users' private documents.
    """
    processed = query_processing.process(body.query, body.previous_questions)
    funds = resolve_fund_mentions(db, processed.retrieval_text)
    result = retrieve(db, settings, processed, Filters(admin.id), mode=body.mode,
                      mentioned_fund_ids=[f.id for f in funds])
    prompt = generation.build_user_prompt(processed.original, result.selected, [], result.conflicts)
    return {
        "query": processed.original, "retrieval_query": processed.retrieval_text,
        "is_follow_up": processed.is_follow_up, "mode": body.mode,
        "query_lexemes": result.query_lexemes, "mentioned_funds": [f.scheme_code for f in funds],
        "timings_ms": {k: round(v, 2) for k, v in result.timings_ms.items()},
        "candidates": [{
            "chunk_id": c.chunk_id, "document": c.document_title, "page_start": c.page_start,
            "page_end": c.page_end, "section": c.section_heading, "vector_similarity": c.vector_similarity,
            "vector_rank": c.vector_rank, "lexical_score": c.lexical_score, "lexical_rank": c.lexical_rank,
            "lexical_coverage": round(c.lexical_coverage, 4), "rrf_score": round(c.rrf_score, 6),
            "boost": c.boost, "rerank_score": c.rerank_score, "final_score": round(c.final_score, 6),
            "supported": c.supported, "selected": any(s.chunk_id == c.chunk_id for s in result.selected),
            "excerpt": c.content[:300],
        } for c in result.candidates],
        "conflicts": result.conflicts,
        "settings": {k: getattr(settings, k) for k in (
            "retrieval_candidates", "retrieval_top_k", "rrf_k", "rrf_vector_weight", "rrf_lexical_weight",
            "answer_min_coverage", "answer_min_similarity", "include_min_coverage", "include_coverage_ratio",
            "include_similarity_margin", "context_word_budget", "max_chunks_per_document")},
        "system_prompt_template": generation.SYSTEM_PROMPT,
        "user_prompt": prompt,
    }


@router.post("/rag/evaluate")
def rag_evaluate(db: DbSession, settings: AppSettings, admin: AdminUser) -> dict:
    enforce_rate_limit(f"rag-eval:{admin.id}", 2, 300)
    report = evaluation.run_evaluation(db, settings, persist=True)
    return {"summary": report["summary"], "config": report["config"],
            "markdown": evaluation.render_markdown(report)}


@router.get("/rag/evaluations/latest")
def latest_evaluation(db: DbSession, _: AdminUser) -> dict:
    run = db.scalar(select(RagEvaluationRun).order_by(RagEvaluationRun.created_at.desc()).limit(1))
    if run is None:
        raise NotFoundError("No evaluation has been run yet.")
    return {"id": str(run.id), "created_at": run.created_at.isoformat(), "config": run.config,
            "summary": run.summary, "per_query": run.per_query}


@router.get("/audit")
def audit_log(db: DbSession, _: AdminUser, page: Annotated[int, Query(ge=1)] = 1,
              page_size: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    total = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": e.id, "event_type": e.event_type, "user_id": str(e.user_id) if e.user_id else None,
                       "ip_address": e.ip_address, "details": e.details, "created_at": e.created_at.isoformat()}
                      for e in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/users")
def list_users(db: DbSession, _: AdminUser) -> dict:
    rows = db.scalars(select(User).order_by(User.created_at)).all()
    return {"items": [{"id": str(u.id), "email": u.email, "display_name": u.display_name, "role": u.role,
                       "is_active": u.is_active, "created_at": u.created_at.isoformat()} for u in rows]}


class UserUpdate(BaseModel):
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, db: DbSession, admin: AdminUser) -> dict:
    target = db.get(User, user_id)
    if target is None:
        raise NotFoundError("User not found.")
    if target.id == admin.id and (body.role == "user" or body.is_active is False):
        raise ValidationFailedError("You cannot demote or deactivate your own account.")
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    audit.record(db, "admin.user_updated", user_id=admin.id, ip_address=client_ip(request),
                 details={"target": str(user_id), **body.model_dump(exclude_none=True)})
    return {"id": str(target.id), "role": target.role, "is_active": target.is_active}


class AmfiRegister(BaseModel):
    scheme_code: str = Field(pattern=r"^\d{3,8}$")
    category: str = Field(min_length=2, max_length=80)
    asset_class: Literal["equity", "debt", "hybrid", "other"]


@router.post("/amfi/register")
def amfi_register(body: AmfiRegister, db: DbSession, settings: AppSettings, admin: AdminUser) -> dict:
    fund = amfi.register_scheme(db, settings, body.scheme_code, category=body.category, asset_class=body.asset_class)
    audit.record(db, "data.amfi_registered", user_id=admin.id, details={"scheme_code": body.scheme_code})
    return {"id": fund.id, "scheme_code": fund.scheme_code, "name": fund.name}


@router.post("/amfi/sync")
def amfi_sync(db: DbSession, settings: AppSettings, admin: AdminUser) -> dict:
    enforce_rate_limit(f"amfi:{admin.id}", 3, 300)
    result = amfi.sync_latest_navs(db, settings)
    audit.record(db, "data.amfi_synced", user_id=admin.id, details={"updated": len(result["updated"])})
    return result
