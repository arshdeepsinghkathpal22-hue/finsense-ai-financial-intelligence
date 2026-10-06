"""AI research assistant endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUser, DbSession, user_rate_limit
from app.core.errors import NotFoundError
from app.models import Conversation, Message, User
from app.services.assistant.orchestrator import answer_question

router = APIRouter(prefix="/assistant", tags=["assistant"])


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    conversation_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


@router.post("/query")
def query(body: QueryRequest, db: DbSession, settings: AppSettings,
          user: Annotated[User, Depends(user_rate_limit("assistant"))]) -> dict:
    return answer_question(db, settings, user, body.question, conversation_id=body.conversation_id,
                           document_ids=body.document_ids)


def _owned(db, user: User, conversation_id: uuid.UUID) -> Conversation:  # type: ignore[no-untyped-def]
    convo = db.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise NotFoundError("Conversation not found.")
    return convo


@router.get("/conversations")
def list_conversations(db: DbSession, user: CurrentUser) -> dict:
    rows = db.scalars(select(Conversation).where(Conversation.user_id == user.id)
                      .order_by(Conversation.updated_at.desc()).limit(100)).all()
    return {"items": [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at.isoformat()} for c in rows]}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    convo = _owned(db, user, conversation_id)
    messages = db.scalars(select(Message).where(Message.conversation_id == convo.id)
                          .order_by(Message.created_at)).all()
    return {"id": str(convo.id), "title": convo.title,
            "messages": [{"id": str(m.id), "role": m.role, "content": m.content, "payload": m.payload,
                          "created_at": m.created_at.isoformat()} for m in messages]}


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    db.delete(_owned(db, user, conversation_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
