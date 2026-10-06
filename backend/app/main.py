"""FastAPI application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, assistant, auth, documents, funds, insights, portfolios, system
from app.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    for directory in (settings.upload_dir, settings.model_dir, settings.cache_dir):
        directory.mkdir(parents=True, exist_ok=True)

    docs = settings.api_docs_enabled
    app = FastAPI(
        title="FinSense AI API",
        version="1.0.0",
        description="Financial analytics, portfolio optimisation, ML forecasting and RAG research API. "
                    "Decision support only - not investment advice.",
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    install_error_handlers(app)
    max_body = max(settings.max_upload_mb, settings.max_csv_mb) * 1024 * 1024 + 1024 * 1024
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_body)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.app_env == "production")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    api = APIRouter(prefix=API_PREFIX)
    for module in (system, auth, funds, insights, portfolios, documents, assistant, admin):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
