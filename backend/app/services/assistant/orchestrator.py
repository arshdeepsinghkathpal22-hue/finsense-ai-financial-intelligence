"""Answers a research question by combining analytics tools and document retrieval."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import NotFoundError
from app.models import Conversation, Fund, Message, Portfolio, User
from app.services.assistant import router
from app.services.assistant.tools import ToolContext, execute
from app.services.fund_mentions import resolve_fund_mentions
from app.services.rag import generation, query, retrieval
from app.services.rag.generation import Calculation, GeneratedAnswer
from app.services.rag.llm import ChatMessage, get_llm_client

HISTORY_TURNS = 6


def _conversation(db: Session, user: User, conversation_id: uuid.UUID | None, question: str) -> Conversation:
    if conversation_id is not None:
        convo = db.get(Conversation, conversation_id)
        if convo is None or convo.user_id != user.id:  # never reveal whether it exists
            raise NotFoundError("Conversation not found.")
        return convo
    convo = Conversation(user_id=user.id, title=question[:117] + ("..." if len(question) > 117 else ""))
    db.add(convo)
    db.commit()  # committed up front so a failing tool's rollback cannot discard it
    return convo


def _history(db: Session, convo: Conversation) -> list[Message]:
    rows = db.scalars(select(Message).where(Message.conversation_id == convo.id)
                      .order_by(Message.created_at.desc()).limit(HISTORY_TURNS)).all()
    return list(reversed(rows))


def _compose_without_llm(question: str, calcs: list[Calculation], doc_answer: GeneratedAnswer) -> str:
    parts = []
    if calcs:
        parts.append("FinSense calculations:")
        for calc in calcs:
            parts.append(f"{calc.title} [{calc.marker}]")
            parts += [f"- {line}" for line in calc.lines]
    if doc_answer.mode == "extractive" and doc_answer.text:
        parts.append("")
        parts.append(doc_answer.text)
    if not parts:
        return generation.INSUFFICIENT_MESSAGE
    return "\n".join(parts).strip()


def answer_question(
    db: Session, settings: Settings, user: User, question: str, *, conversation_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None, llm=None,  # type: ignore[no-untyped-def]
) -> dict:
    started = time.perf_counter()
    llm = llm if llm is not None else get_llm_client(settings)
    convo = _conversation(db, user, conversation_id, question)
    history = _history(db, convo)
    previous_questions = [m.content for m in history if m.role == "user"]
    processed = query.process(question, previous_questions)

    funds: list[Fund] = resolve_fund_mentions(db, processed.original)
    if not funds and processed.is_follow_up:
        funds = resolve_fund_mentions(db, processed.retrieval_text)
    portfolio_names = [p.name for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id))]

    plan = router.plan_with_llm(llm, processed.original, funds, portfolio_names) if llm is not None else None
    if plan is None:
        plan = router.rule_based_plan(processed.original, funds, portfolio_names)

    ctx = ToolContext(db, settings, user)
    calcs: list[Calculation] = []
    tool_errors: list[str] = []
    for call in plan.calls:
        executed = execute(ctx, call)
        if executed.result is None:
            tool_errors.append(executed.error or "Tool failed.")
            continue
        calcs.append(Calculation(f"T{len(calcs) + 1}", executed.result.title, executed.result.lines,
                                 {"tool": call.name, "args": call.args}))

    retrieval_result = None
    evidence: list[retrieval.Candidate] = []
    if plan.use_documents:
        retrieval_result = retrieval.retrieve(
            db, settings, processed, retrieval.Filters(user.id, document_ids=document_ids),
            mentioned_fund_ids=[f.id for f in funds],
        )
        evidence = retrieval_result.selected

    chat_history = [ChatMessage(m.role, m.content) for m in history[-4:]]
    if llm is not None and (evidence or calcs):
        answer = generation.safe_generate(llm, settings, processed.original, evidence, calcs,
                                          retrieval_result.conflicts if retrieval_result else [], chat_history)
        if answer.mode == "analytics_only" or (answer.mode == "extractive" and calcs):
            answer.text = _compose_without_llm(processed.original, calcs, answer)
    else:
        doc_answer = generation.extractive_answer(processed.original, evidence) if evidence else \
            GeneratedAnswer("", "insufficient_evidence", [], [], [])
        text = _compose_without_llm(processed.original, calcs, doc_answer)
        mode = "extractive" if evidence else ("analytics_only" if calcs else "insufficient_evidence")
        answer = GeneratedAnswer(text, mode, doc_answer.used_sources, [c.marker for c in calcs],
                                 doc_answer.warnings)

    used = set(answer.used_sources) if answer.mode == "llm" else {generation.source_marker(i)
                                                                   for i in range(len(evidence))}
    sources = []
    for i, cand in enumerate(evidence):
        marker = generation.source_marker(i)
        sources.append({
            "marker": marker, "cited": marker in used, "chunk_id": cand.chunk_id, "document_id": cand.document_id,
            "document_title": cand.document_title, "filename": cand.filename, "doc_type": cand.doc_type,
            "as_of_date": cand.as_of_date, "page_start": cand.page_start, "page_end": cand.page_end,
            "section": cand.section_heading, "excerpt": cand.content, "is_synthetic": cand.is_synthetic,
            "flags": cand.flags,
        })

    warnings = list(answer.warnings) + tool_errors + plan.notes
    if any("instruction_like_text" in s["flags"] for s in sources):
        warnings.append("A retrieved passage contains instruction-like text; it was treated as data only.")
    limitations = []
    if any(s["is_synthetic"] for s in sources) or any(
            "SYNTHETIC" in line for c in calcs for line in c.lines):
        limitations.append("Some figures come from SYNTHETIC demonstration data, not real market data.")
    if llm is None:
        limitations.append("No language model is configured: document answers quote the most relevant "
                           "passages verbatim instead of writing a summary.")
    limitations.append("Decision support only: historical figures and model estimates do not guarantee "
                       "future returns.")

    payload = {
        "conversation_id": str(convo.id),
        "answer": answer.text,
        "mode": answer.mode,
        "model": answer.model,
        "planner": plan.planner,
        "sources": sources,
        "calculations": [{"marker": c.marker, "title": c.title, "lines": c.lines, **c.data} for c in calcs],
        "conflicts": retrieval_result.conflicts if retrieval_result else [],
        "warnings": warnings,
        "limitations": limitations,
        "funds": [{"id": f.id, "scheme_code": f.scheme_code, "name": f.name} for f in funds],
        "retrieval": {
            "query": processed.retrieval_text, "is_follow_up": processed.is_follow_up,
            "candidates": len(retrieval_result.candidates) if retrieval_result else 0,
            "selected": len(evidence),
        },
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    db.add(Message(conversation_id=convo.id, role="user", content=processed.original, payload={}))
    db.add(Message(conversation_id=convo.id, role="assistant", content=answer.text,
                   payload={k: payload[k] for k in ("mode", "sources", "calculations", "conflicts", "warnings",
                                                   "limitations", "funds")}))
    db.commit()
    return payload
