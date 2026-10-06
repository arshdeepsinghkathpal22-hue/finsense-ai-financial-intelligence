"""User registration, login sessions and password reset."""

from __future__ import annotations

import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import security
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.models import PasswordResetToken, User, UserSession

logger = logging.getLogger("finsense.auth")

RESET_TOKEN_TTL = timedelta(minutes=30)


def normalise_email(email: str) -> str:
    return email.strip().lower()


def register_user(
    db: Session, *, email: str, password: str, display_name: str, role: str = "user"
) -> User:
    problems = security.validate_password_strength(password)
    if problems:
        raise ValidationFailedError("Password does not meet requirements.", {"problems": problems})
    email = normalise_email(email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("An account with this e-mail already exists.")
    user = User(
        email=email,
        password_hash=security.hash_password(password),
        display_name=display_name.strip(),
        role=role,
        preferences={},
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, *, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == normalise_email(email)))
    # verify_password runs a real Argon2 check even for unknown users so the
    # response time does not reveal which e-mails are registered.
    valid = security.verify_password(user.password_hash if user else None, password)
    if not valid or user is None or not user.is_active:
        raise AuthenticationError("Invalid e-mail or password.")
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)
        db.commit()
    return user


@dataclass(frozen=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    expires_at: datetime


def create_session(
    db: Session, settings: Settings, user: User, *, ip: str | None, user_agent: str | None
) -> IssuedSession:
    session_token = security.new_token()
    csrf_token = security.new_token()
    expires_at = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=security.hash_token(session_token),
            csrf_token_hash=security.hash_token(csrf_token),
            expires_at=expires_at,
            ip_address=ip,
            user_agent=(user_agent or "")[:256],
        )
    )
    # Opportunistic clean-up of this user's expired sessions.
    db.execute(
        delete(UserSession).where(
            UserSession.user_id == user.id, UserSession.expires_at < datetime.now(UTC)
        )
    )
    db.commit()
    return IssuedSession(session_token, csrf_token, expires_at)


def resolve_session(db: Session, session_token: str) -> UserSession | None:
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == security.hash_token(session_token))
    )
    if session is None:
        return None
    now = datetime.now(UTC)
    if session.expires_at <= now or not session.user.is_active:
        db.delete(session)
        db.commit()
        return None
    # Avoid a write on every request: refresh last_seen at most once a minute.
    if now - session.last_seen_at > timedelta(minutes=1):
        session.last_seen_at = now
        db.commit()
    return session


def revoke_session(db: Session, session_id: uuid.UUID) -> None:
    db.execute(delete(UserSession).where(UserSession.id == session_id))
    db.commit()


def revoke_all_sessions(db: Session, user_id: uuid.UUID) -> int:
    result = db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    db.commit()
    return result.rowcount or 0


def change_password(db: Session, user: User, *, current_password: str, new_password: str) -> None:
    if not security.verify_password(user.password_hash, current_password):
        raise AuthenticationError("Current password is incorrect.")
    problems = security.validate_password_strength(new_password)
    if problems:
        raise ValidationFailedError("Password does not meet requirements.", {"problems": problems})
    user.password_hash = security.hash_password(new_password)
    # Changing the password signs out every existing session.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()


def request_password_reset(db: Session, settings: Settings, email: str) -> None:
    """Sends a reset link if the account exists. Always behaves the same to the caller."""
    if not settings.email_configured:
        raise ServiceUnavailableError(
            "Password reset by e-mail is not configured on this server. "
            "Ask an administrator to reset your password."
        )
    user = db.scalar(select(User).where(User.email == normalise_email(email)))
    if user is None or not user.is_active:
        return
    token = security.new_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=security.hash_token(token),
            expires_at=datetime.now(UTC) + RESET_TOKEN_TTL,
        )
    )
    db.commit()
    link = f"{settings.public_app_url.rstrip('/')}/reset-password?token={token}"
    send_email(
        settings,
        to=user.email,
        subject="FinSense AI password reset",
        body=(
            "A password reset was requested for your FinSense AI account.\n\n"
            f"Open this link within 30 minutes to choose a new password:\n{link}\n\n"
            "If you did not request this, you can ignore this e-mail."
        ),
    )


def confirm_password_reset(db: Session, *, token: str, new_password: str) -> None:
    record = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == security.hash_token(token)
        )
    )
    now = datetime.now(UTC)
    if record is None or record.used_at is not None or record.expires_at <= now:
        raise ValidationFailedError("This reset link is invalid or has expired.")
    problems = security.validate_password_strength(new_password)
    if problems:
        raise ValidationFailedError("Password does not meet requirements.", {"problems": problems})
    user = db.get(User, record.user_id)
    if user is None:
        raise ValidationFailedError("This reset link is invalid or has expired.")
    user.password_hash = security.hash_password(new_password)
    record.used_at = now
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()


def send_email(settings: Settings, *, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password is not None:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)
    logger.info("Sent password reset e-mail")
