"""Health checks and non-sensitive configuration status."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.core.errors import ServiceUnavailableError
from app.models import Document, Fund, NavObservation

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: DbSession) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # any driver error means "not ready"
        raise ServiceUnavailableError("Database is not reachable.") from exc
    return {"status": "ready", "database": "ok"}


@router.get("/system/status")
def system_status(db: DbSession, settings: AppSettings, _: CurrentUser) -> dict:
    """What is configured - never secrets, hosts or credentials."""
    synthetic = db.scalar(select(func.count()).select_from(Fund).where(Fund.is_synthetic.is_(True))) or 0
    real = db.scalar(select(func.count()).select_from(Fund).where(Fund.is_synthetic.is_(False))) or 0
    latest = db.scalar(select(func.max(NavObservation.obs_date)))
    return {
        "app": {"name": settings.app_name, "environment": settings.app_env},
        "llm": {"configured": settings.llm_configured, "provider": settings.llm_provider,
                "model": settings.llm_model or None,
                "mode_without_llm": "extractive answers (verbatim passages with citations)"},
        "embeddings": {"provider": settings.embedding_provider, "model": settings.embedding_model,
                       "dimensions": settings.embedding_dim},
        "reranker": settings.reranker,
        "live_data": {"amfi_enabled": settings.amfi_enabled},
        "email": {"password_reset_available": settings.email_configured},
        "conventions": {"risk_free_rate": settings.risk_free_rate,
                        "trading_days_per_year": settings.trading_days_per_year,
                        "stale_after_days": settings.stale_after_days},
        "data": {"synthetic_funds": synthetic, "real_funds": real,
                 "latest_nav_date": latest.isoformat() if latest else None,
                 "documents": db.scalar(select(func.count()).select_from(Document)) or 0},
        "notice": "Synthetic demonstration data is fictional and is labelled wherever it is shown."
        if synthetic else None,
    }
