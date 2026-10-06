"""Query normalisation and follow-up handling.

Normalisation is intentionally light: financial questions depend on exact
scheme codes, dates, percentages and names, so nothing is lower-cased,
stemmed or removed here (the lexical index does its own stemming).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_QUERY_CHARS = 1000

_FOLLOW_UP_START = re.compile(r"^(and|also|what about|how about|and what|then|why|so)\b", re.IGNORECASE)
_ANAPHORA = re.compile(
    r"\b(it|its|it's|this|that|these|those|they|them|their|the fund|the scheme|this fund|that fund|"
    r"same fund|the document|the factsheet|the report)\b",
    re.IGNORECASE,
)
_PERIOD_HINT = re.compile(
    r"\b(19|20)\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|\bfy\s?\d{2}",
    re.IGNORECASE,
)
_LATEST_HINT = re.compile(r"\b(latest|current|currently|now|recent|most recent|today)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ProcessedQuery:
    original: str
    retrieval_text: str
    is_follow_up: bool
    mentions_period: bool
    wants_latest: bool


def normalise(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_QUERY_CHARS]


def looks_like_follow_up(question: str) -> bool:
    words = question.split()
    if len(words) > 14:
        return False
    return bool(_FOLLOW_UP_START.match(question) or _ANAPHORA.search(question))


def process(question: str, previous_questions: list[str] | None = None) -> ProcessedQuery:
    """Builds the retrieval query.

    For a short follow-up ("what about its expense ratio?") the previous
    question from the *same conversation* is prepended so the retriever
    knows which fund or document "its" refers to. Conversation history is
    only ever used as query text; it never widens document access.
    """
    clean = normalise(question)
    follow_up = bool(previous_questions) and looks_like_follow_up(clean)
    retrieval_text = f"{normalise(previous_questions[-1])} {clean}" if follow_up else clean
    return ProcessedQuery(
        original=clean,
        retrieval_text=retrieval_text[: MAX_QUERY_CHARS * 2],
        is_follow_up=follow_up,
        mentions_period=bool(_PERIOD_HINT.search(clean)),
        wants_latest=bool(_LATEST_HINT.search(clean)),
    )
