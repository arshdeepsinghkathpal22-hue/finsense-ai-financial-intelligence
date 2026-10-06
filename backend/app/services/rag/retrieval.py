"""Hybrid retrieval: pgvector semantic search + PostgreSQL full-text search.

Pipeline::

    query -> (vector top-N, lexical top-N)      # both filtered by access rules in SQL
          -> Reciprocal Rank Fusion
          -> small boosts (named fund, latest reporting period)
          -> optional cross-encoder rerank
          -> evidence gate (lexical coverage or vector similarity)
          -> de-duplication and context selection

Access control is part of every SQL statement (``visibility = 'shared' OR
owner_id = :user_id``), so chunks a user may not read are never loaded into
Python, let alone passed to a language model.

Reciprocal Rank Fusion (Cormack et al., 2009) combines rankings without
having to calibrate cosine similarities against ``ts_rank`` scores, which
live on unrelated scales: ``score = sum(1 / (k + rank))`` with k = 60.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.rag.embeddings import get_embedder
from app.services.rag.query import ProcessedQuery

Mode = Literal["hybrid", "vector", "lexical"]
_SAFE_LEXEME = re.compile(r"^[\w.\-/]+$")


@dataclass
class Filters:
    user_id: uuid.UUID
    document_ids: list[uuid.UUID] | None = None
    doc_types: list[str] | None = None
    fund_ids: list[int] | None = None


@dataclass
class Candidate:
    chunk_id: str
    document_id: str
    document_title: str
    filename: str
    doc_type: str
    as_of_date: str | None
    fund_id: int | None
    is_synthetic: bool
    page_start: int
    page_end: int
    section_heading: str | None
    content: str
    is_table: bool
    flags: list[str]
    vector_similarity: float | None = None
    vector_rank: int | None = None
    lexical_score: float | None = None
    lexical_rank: int | None = None
    lexical_coverage: float = 0.0
    rrf_score: float = 0.0
    boost: float = 1.0
    rerank_score: float | None = None
    final_score: float = 0.0
    supported: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RetrievalResult:
    query: str
    mode: str
    candidates: list[Candidate]
    selected: list[Candidate]
    conflicts: list[dict]
    timings_ms: dict[str, float] = field(default_factory=dict)
    query_lexemes: list[str] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return bool(self.selected)


def _access_sql(filters: Filters, params: dict) -> str:
    params["user_id"] = filters.user_id
    clauses = ["d.status = 'indexed'", "(d.visibility = 'shared' OR d.owner_id = :user_id)"]
    if filters.document_ids:
        clauses.append("d.id = ANY(:document_ids)")
        params["document_ids"] = list(filters.document_ids)
    if filters.doc_types:
        clauses.append("d.doc_type = ANY(:doc_types)")
        params["doc_types"] = list(filters.doc_types)
    if filters.fund_ids:
        clauses.append("d.fund_id = ANY(:fund_ids)")
        params["fund_ids"] = list(filters.fund_ids)
    return " AND ".join(clauses)


def _vector_literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


# Words that describe the *request* rather than the content being asked
# about. They are kept in the vector query but dropped from lexical matching,
# where they would otherwise count as unmatched informative terms.
_REQUEST_WORDS = (
    "say said says tell show give explain describe mention mentioned state stated according latest current "
    "currently recent please much many know list provide detail details info information summarise "
    "summarize happen happened"
)


def query_lexemes(db: Session, query: str) -> list[str]:
    rows = db.execute(
        text(
            "SELECT lex FROM unnest(tsvector_to_array(to_tsvector('english', :q))) AS lex "
            "WHERE lex <> ALL(tsvector_to_array(to_tsvector('english', :request_words)))"
        ),
        {"q": query, "request_words": _REQUEST_WORDS},
    )
    return [lex for (lex,) in rows if _SAFE_LEXEME.match(lex) and "'" not in lex][:40]


def _or_tsquery(lexemes: list[str]) -> str:
    return " | ".join(f"'{lex}'" for lex in lexemes)


def vector_search(db: Session, vector: np.ndarray, filters: Filters, limit: int) -> list[tuple[str, float]]:
    params: dict = {"qvec": _vector_literal(vector), "limit": limit}
    where = _access_sql(filters, params)
    # Larger ef_search keeps recall high when access filters discard index hits.
    db.execute(text("SET LOCAL hnsw.ef_search = 200"))
    rows = db.execute(
        text(
            f"""
            SELECT c.id::text, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS similarity
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE {where}
            ORDER BY c.embedding <=> CAST(:qvec AS vector), c.content_hash, d.title, c.chunk_index
            LIMIT :limit
            """
        ),
        params,
    ).all()
    return [(cid, float(sim)) for cid, sim in rows]


def lexical_search(db: Session, lexemes: list[str], filters: Filters, limit: int) -> list[tuple[str, float]]:
    if not lexemes:
        return []
    params: dict = {"tsq": _or_tsquery(lexemes), "limit": limit}
    where = _access_sql(filters, params)
    rows = db.execute(
        text(
            f"""
            SELECT c.id::text, ts_rank_cd(c.search_vector, to_tsquery('simple', :tsq), 32) AS score
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE {where} AND c.search_vector @@ to_tsquery('simple', :tsq)
            ORDER BY score DESC, c.content_hash, d.title, c.chunk_index
            LIMIT :limit
            """
        ),
        params,
    ).all()
    return [(cid, float(score)) for cid, score in rows]


def _document_frequencies(db: Session, lexemes: list[str], filters: Filters) -> tuple[int, dict[str, int]]:
    """Corpus size and per-lexeme chunk counts over the user's accessible chunks."""
    if not lexemes:
        return 0, {}
    params: dict = {}
    # IDF is measured over everything the user can read, even when the
    # search itself is restricted to particular documents.
    where = _access_sql(Filters(filters.user_id), params)
    columns = []
    for i, lex in enumerate(lexemes):
        params[f"l{i}"] = f"'{lex}'"
        columns.append(f"count(*) FILTER (WHERE c.search_vector @@ to_tsquery('simple', :l{i})) AS d{i}")
    row = db.execute(
        text(f"SELECT count(*) AS n, {', '.join(columns)} "
             f"FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE {where}"),
        params,
    ).one()
    return int(row[0]), {lex: int(row[i + 1]) for i, lex in enumerate(lexemes)}


def _load_candidates(db: Session, chunk_ids: list[str], filters: Filters) -> dict[str, tuple[Candidate, set]]:
    if not chunk_ids:
        return {}
    params: dict = {"ids": [uuid.UUID(c) for c in chunk_ids]}
    where = _access_sql(filters, params)  # re-checked: never trust ids alone
    rows = db.execute(
        text(
            f"""
            SELECT c.id::text, d.id::text, d.title, d.original_filename, d.doc_type, d.as_of_date,
                   d.fund_id, d.is_synthetic, c.page_start, c.page_end, c.section_heading, c.content,
                   c.is_table, c.flags, tsvector_to_array(c.search_vector)
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.id = ANY(:ids) AND {where}
            """
        ),
        params,
    ).all()
    out = {}
    for r in rows:
        as_of = r[5].isoformat() if isinstance(r[5], date) else None
        out[r[0]] = (
            Candidate(chunk_id=r[0], document_id=r[1], document_title=r[2], filename=r[3], doc_type=r[4],
                      as_of_date=as_of, fund_id=r[6], is_synthetic=r[7], page_start=r[8], page_end=r[9],
                      section_heading=r[10], content=r[11], is_table=r[12], flags=list(r[13] or [])),
            set(r[14] or []),
        )
    return out


def _shingles(text_: str, n: int = 5) -> set[tuple[str, ...]]:
    words = text_.lower().split()
    return {tuple(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def _near_duplicate(a: Candidate, b: Candidate, threshold: float = 0.8) -> bool:
    sa, sb = _shingles(a.content), _shingles(b.content)
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= threshold


def retrieve(
    db: Session, settings: Settings, processed: ProcessedQuery, filters: Filters, *,
    mode: Mode = "hybrid", top_k: int | None = None, mentioned_fund_ids: list[int] | None = None,
    reranker=None,  # type: ignore[no-untyped-def]
) -> RetrievalResult:
    """Retrieves evidence for a processed query.

    ``mentioned_fund_ids`` are the funds the question names. Passages from
    documents about *other* funds are then not accepted as evidence (they
    may still be listed as candidates); documents not linked to any fund
    remain eligible.
    """
    timings: dict[str, float] = {}
    top_k = top_k or settings.retrieval_top_k
    n_candidates = settings.retrieval_candidates
    query = processed.retrieval_text

    t0 = time.perf_counter()
    lexemes = query_lexemes(db, query)
    vector_hits: list[tuple[str, float]] = []
    if mode in ("hybrid", "vector"):
        qvec = get_embedder().embed([query])[0]
        timings["embed_query"] = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        vector_hits = vector_search(db, qvec, filters, n_candidates)
        timings["vector_search"] = (time.perf_counter() - t1) * 1000
    lexical_hits: list[tuple[str, float]] = []
    if mode in ("hybrid", "lexical"):
        t1 = time.perf_counter()
        # Over-fetch with the cheap OR-query, then re-rank by IDF-weighted
        # coverage below (ts_rank alone ignores how rare a term is).
        lexical_hits = lexical_search(db, lexemes, filters, n_candidates * 3)
        timings["lexical_search"] = (time.perf_counter() - t1) * 1000

    t1 = time.perf_counter()
    loaded = _load_candidates(db, list({cid for cid, _ in vector_hits} | {cid for cid, _ in lexical_hits}), filters)
    corpus_size, dfs = _document_frequencies(db, lexemes, filters)
    idf = {lex: math.log((corpus_size + 1) / (df + 1)) + 1.0 for lex, df in dfs.items()}
    total_idf = sum(idf.values())

    def coverage(cid: str) -> float:
        if total_idf == 0 or cid not in loaded:
            return 0.0
        chunk_lexemes = loaded[cid][1]
        return sum(idf[lex] for lex in lexemes if lex in chunk_lexemes) / total_idf

    def stable(cid: str) -> tuple:
        # Ties are broken by content, not by random chunk ids, so results are
        # reproducible when the same documents are indexed again.
        if cid not in loaded:
            return ("", "", 0, cid)
        cand = loaded[cid][0]
        return (cand.content, cand.document_title, cand.page_start or 0, cid)

    lexical_hits = sorted(lexical_hits,
                          key=lambda hit: (-round(coverage(hit[0]), 6), -round(hit[1], 9), stable(hit[0])))
    lexical_hits = lexical_hits[:n_candidates]

    candidates: dict[str, Candidate] = {}
    for rank, (cid, sim) in enumerate(vector_hits, start=1):
        if cid in loaded:
            cand = loaded[cid][0]
            cand.vector_similarity, cand.vector_rank = sim, rank
            cand.rrf_score += settings.rrf_vector_weight / (settings.rrf_k + rank)
            candidates[cid] = cand
    for rank, (cid, score) in enumerate(lexical_hits, start=1):
        if cid in loaded:
            cand = loaded[cid][0]
            cand.lexical_score, cand.lexical_rank = score, rank
            cand.rrf_score += settings.rrf_lexical_weight / (settings.rrf_k + rank)
            candidates[cid] = cand
    for cid, cand in candidates.items():
        cand.lexical_coverage = coverage(cid)

    _apply_boosts(list(candidates.values()), processed, mentioned_fund_ids or [])
    ranked = sorted(candidates.values(), key=lambda c: (-round(c.rrf_score * c.boost, 12), stable(c.chunk_id)))
    if reranker is not None and ranked:
        t2 = time.perf_counter()
        scores = reranker.score(processed.original, [c.content for c in ranked])
        for cand, score in zip(ranked, scores, strict=True):
            cand.rerank_score = float(score)
        ranked.sort(key=lambda c: -(c.rerank_score or 0.0))
        timings["rerank"] = (time.perf_counter() - t2) * 1000
    for cand in ranked:
        cand.final_score = cand.rrf_score * cand.boost if reranker is None else float(cand.rerank_score or 0)
    apply_evidence_gate(ranked, settings, mode, mentioned_fund_ids or [])
    # Candidates that pass the evidence gate come first; order is otherwise kept.
    ranked.sort(key=lambda c: not c.supported)
    timings["fusion"] = (time.perf_counter() - t1) * 1000

    selected = select_context(ranked, top_k=top_k, word_budget=settings.context_word_budget,
                              max_per_document=settings.max_chunks_per_document)
    timings["total"] = (time.perf_counter() - t0) * 1000
    return RetrievalResult(query=query, mode=mode, candidates=ranked, selected=selected,
                           conflicts=detect_conflicts(selected), timings_ms=timings, query_lexemes=lexemes)


def apply_evidence_gate(ranked: list[Candidate], settings: Settings, mode: str,
                        mentioned_fund_ids: list[int] | None = None) -> None:
    """Marks which candidates may be used as evidence.

    Step 1 - answerability: the best candidate must either contain most of the
    query's informative (IDF-weighted) terms or be very similar to the query.
    Otherwise nothing is used and the assistant reports insufficient evidence.

    Step 2 - inclusion: other candidates are admitted when they are close to
    the best one on the same signals, so multi-part questions can draw on
    several passages without admitting merely "on-topic" text.

    When the question names specific funds, passages from documents about
    other funds are never eligible.

    Thresholds live in Settings and were chosen on the dev split of
    sample_data/eval/rag_eval_set.json; the holdout split measures how well
    they generalise (docs/RAG_EVALUATION.md).
    """
    use_lexical = mode != "vector"
    use_vector = mode != "lexical"
    mentioned = set(mentioned_fund_ids or [])
    eligible = [c for c in ranked if not mentioned or c.fund_id is None or c.fund_id in mentioned]
    for cand in ranked:
        cand.supported = False
    best_cov = max((c.lexical_coverage for c in eligible), default=0.0) if use_lexical else 0.0
    best_sim = max((c.vector_similarity or 0.0 for c in eligible), default=0.0) if use_vector else 0.0
    answerable = best_cov >= settings.answer_min_coverage or best_sim >= settings.answer_min_similarity
    cov_floor = max(settings.include_min_coverage, settings.include_coverage_ratio * best_cov)
    sim_floor = max(settings.answer_min_similarity - settings.include_similarity_margin,
                    best_sim - settings.include_similarity_margin)
    for cand in eligible:
        lexical_ok = use_lexical and best_cov >= settings.answer_min_coverage and cand.lexical_coverage >= cov_floor
        vector_ok = use_vector and best_sim >= settings.answer_min_similarity \
            and (cand.vector_similarity or 0.0) >= sim_floor
        cand.supported = answerable and (lexical_ok or vector_ok)


def _apply_boosts(candidates: list[Candidate], processed: ProcessedQuery, fund_ids: list[int]) -> None:
    # Prefer the newest document of each (fund, type) unless the question
    # names a specific period - then every period competes on relevance alone.
    latest: dict[tuple, str] = {}
    for cand in candidates:
        key = (cand.fund_id, cand.doc_type)
        if cand.fund_id is not None and cand.as_of_date and (key not in latest or cand.as_of_date > latest[key]):
            latest[key] = cand.as_of_date
    for cand in candidates:
        if fund_ids and cand.fund_id in fund_ids:
            cand.boost *= 1.15
        key = (cand.fund_id, cand.doc_type)
        if not processed.mentions_period and cand.as_of_date and key in latest and cand.as_of_date == latest[key]:
            cand.boost *= 1.25 if processed.wants_latest else 1.05


def select_context(ranked: list[Candidate], *, top_k: int, word_budget: int,
                   max_per_document: int = 4) -> list[Candidate]:
    """Picks supported, non-redundant passages within the context budget."""
    selected: list[Candidate] = []
    per_document: dict[str, int] = {}
    words = 0
    for cand in ranked:
        if not cand.supported:
            continue
        if any(cand.content == s.content or _near_duplicate(cand, s) for s in selected):
            continue
        if per_document.get(cand.document_id, 0) >= max_per_document:
            continue
        n = len(cand.content.split())
        if selected and words + n > word_budget:
            continue
        selected.append(cand)
        per_document[cand.document_id] = per_document.get(cand.document_id, 0) + 1
        words += n
        if len(selected) >= top_k:
            break
    return selected


def detect_conflicts(selected: list[Candidate]) -> list[dict]:
    """Flags evidence about the same fund drawn from different reporting periods."""
    periods: dict[int, dict[str, str]] = {}
    for cand in selected:
        if cand.fund_id is not None and cand.as_of_date:
            periods.setdefault(cand.fund_id, {})[cand.as_of_date] = cand.document_title
    conflicts = []
    for fund_id, by_date in periods.items():
        if len(by_date) > 1:
            conflicts.append({
                "fund_id": fund_id,
                "periods": [{"as_of_date": d, "document": t} for d, t in sorted(by_date.items())],
                "note": "Evidence comes from documents with different reporting periods; figures may differ "
                        "because they describe different dates.",
            })
    return conflicts
