"""Security audit trail (logins, permission failures, uploads, admin actions)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    db: Session,
    event_type: str,
    *,
    user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    db.add(
        AuditEvent(
            event_type=event_type,
            user_id=user_id,
            ip_address=ip_address,
            details=details or {},
        )
    )
    if commit:
        db.commit()
