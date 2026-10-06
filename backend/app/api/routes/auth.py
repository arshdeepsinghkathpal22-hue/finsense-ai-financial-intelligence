"""Authentication and profile endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AppSettings,
    CurrentUser,
    DbSession,
    client_ip,
    enforce_rate_limit,
    get_current_session,
)
from app.core.errors import AuthenticationError
from app.models import UserSession
from app.services import audit, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class Preferences(BaseModel):
    risk_free_rate: float | None = Field(default=None, ge=0, le=0.2)
    default_period: Literal["1y", "3y", "5y", "max"] | None = None
    risk_profile: Literal["conservative", "moderate", "aggressive"] | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    preferences: dict
    created_at: datetime


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str
    expires_at: datetime


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    preferences: Preferences | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=1, max_length=128)


def _user_out(user) -> UserOut:  # type: ignore[no-untyped-def]
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        preferences=user.preferences or {},
        created_at=user.created_at,
    )


def _set_session_cookies(response: Response, settings, issued) -> None:  # type: ignore[no-untyped-def]
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        issued.session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite=settings.cookie_samesite,
        path="/",
    )
    # Readable by the SPA so it can echo it back in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.secure_cookies,
        samesite=settings.cookie_samesite,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: DbSession, settings: AppSettings) -> UserOut:
    ip = client_ip(request)
    enforce_rate_limit(f"register:{ip}", settings.rate_limit_register_per_hour, 3600)
    user = auth_service.register_user(
        db, email=body.email, password=body.password, display_name=body.display_name
    )
    audit.record(db, "user.registered", user_id=user.id, ip_address=ip)
    return _user_out(user)


@router.post("/login", response_model=SessionOut)
def login(
    body: LoginRequest, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> SessionOut:
    ip = client_ip(request)
    email = auth_service.normalise_email(body.email)
    # Two limits: per address+account (slows guessing one password) and per
    # address (slows spraying many accounts).
    enforce_rate_limit(f"login:{ip}:{email}", settings.rate_limit_login_per_minute, 60)
    enforce_rate_limit(f"login-ip:{ip}", settings.rate_limit_login_per_minute * 4, 60)
    try:
        user = auth_service.authenticate(db, email=email, password=body.password)
    except AuthenticationError:
        audit.record(db, "auth.login_failed", ip_address=ip, details={"email": email})
        raise
    issued = auth_service.create_session(
        db, settings, user, ip=ip, user_agent=request.headers.get("user-agent")
    )
    audit.record(db, "auth.login", user_id=user.id, ip_address=ip)
    _set_session_cookies(response, settings, issued)
    return SessionOut(user=_user_out(user), csrf_token=issued.csrf_token, expires_at=issued.expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: DbSession,
    session: Annotated[UserSession, Depends(get_current_session)],
) -> Response:
    auth_service.revoke_session(db, session.id)
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(response: Response, request: Request, db: DbSession, user: CurrentUser) -> Response:
    count = auth_service.revoke_all_sessions(db, user.id)
    audit.record(db, "auth.logout_all", user_id=user.id, ip_address=client_ip(request),
                 details={"sessions": count})
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return _user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileUpdate, db: DbSession, user: CurrentUser) -> UserOut:
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.preferences is not None:
        merged = dict(user.preferences or {})
        merged.update(body.preferences.model_dump(exclude_none=True))
        user.preferences = merged
    db.commit()
    return _user_out(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> Response:
    enforce_rate_limit(f"change-pw:{user.id}", settings.rate_limit_login_per_minute, 60)
    auth_service.change_password(
        db, user, current_password=body.current_password, new_password=body.new_password
    )
    audit.record(db, "auth.password_changed", user_id=user.id, ip_address=client_ip(request))
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(
    body: ResetRequest, request: Request, db: DbSession, settings: AppSettings
) -> dict:
    ip = client_ip(request)
    enforce_rate_limit(f"reset:{ip}", settings.rate_limit_login_per_minute, 60)
    auth_service.request_password_reset(db, settings, body.email)
    return {"message": "If that account exists, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(
    body: ResetConfirm, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> Response:
    enforce_rate_limit(f"reset-confirm:{client_ip(request)}", settings.rate_limit_login_per_minute, 60)
    auth_service.confirm_password_reset(db, token=body.token, new_password=body.new_password)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
