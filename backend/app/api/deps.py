"""Shared FastAPI dependencies: database session, authentication, CSRF, rate limits."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core import security
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from app.core.rate_limit import limiter
from app.db import get_db
from app.models import User, UserSession
from app.services import auth_service

SESSION_COOKIE = "fs_session"
CSRF_COOKIE = "fs_csrf"
CSRF_HEADER = "X-CSRF-Token"

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def get_current_session(request: Request, db: DbSession) -> UserSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthenticationError("Sign in to continue.")
    session = auth_service.resolve_session(db, token)
    if session is None:
        raise AuthenticationError("Your session has expired. Please sign in again.")
    # Double-submit CSRF check for every state-changing request. The session
    # cookie is SameSite=Lax as well; this is defence in depth.
    if request.method not in _SAFE_METHODS:
        header_token = request.headers.get(CSRF_HEADER, "")
        if not header_token or not security.tokens_match(header_token, session.csrf_token_hash):
            raise PermissionDeniedError("Missing or invalid CSRF token.")
    request.state.user_id = session.user_id
    return session


def get_current_user(session: Annotated[UserSession, Depends(get_current_session)]) -> User:
    return session.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise PermissionDeniedError("Administrator access is required.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def enforce_rate_limit(key: str, limit: int, window_s: float) -> None:
    allowed, retry_after = limiter.hit(key, limit, window_s)
    if not allowed:
        raise RateLimitedError(
            "Too many requests. Please wait before trying again.",
            {"retry_after_s": round(retry_after, 1)},
        )


def user_rate_limit(bucket: str, window_s: float = 60.0) -> Callable[..., User]:
    """Per-user limit for expensive endpoints (model training, RAG, optimisation)."""

    def dependency(user: CurrentUser) -> User:
        limit = get_settings().rate_limit_expensive_per_minute
        enforce_rate_limit(f"{bucket}:{user.id}", limit, window_s)
        return user

    return dependency
