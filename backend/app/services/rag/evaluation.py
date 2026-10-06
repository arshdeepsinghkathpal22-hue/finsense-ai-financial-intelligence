"""Reproducible retrieval evaluation over the sample documents.

Runs every question in ``sample_data/eval/rag_eval_set.json`` against the
real retrieval pipeline in three configurations (hybrid, vector-only,
lexical-only) and reports standard IR metrics:

* Recall@k  - share of the question's labelled facts found in the top k chunks
* Precision@k - share of the top k chunks that contain a labelled fact
* MRR       - mean reciprocal rank of the first relevant chunk
* nDCG@5    - binary-relevance normalised discounted cumulative gain
* Evidence recall - share of facts present in the context actually passed
  to answer generation (after the evidence gate and de-duplication)
* Abstention accuracy - share of unanswerable questions for which no
  evidence was selected (so the assistant says "insufficient evidence")

Answer checks (citation validity, answer contains the expected figure) are
reported separately from ranking quality. Temporary evaluation users and
the private test document are removed afterwards.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import statistics
import time
import uuid
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, Settings
from app.core.errors import ValidationFailedError
from app.models import Document, RagEvaluationRun, User
from app.services.fund_mentions import resolve_fund_mentions
from app.services.rag import citations, generation, indexing
from app.services.rag import query as query_processing
from app.services.rag.retrieval import Filters, retrieve

EVAL_PATH = BACKEND_DIR / "sample_data" / "eval" / "rag_eval_set.json"
MODES = ("hybrid", "vector", "lexical")
KS = (1, 3, 5)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _contains(content: str, snippet: str) -> bool:
    return _norm(snippet) in _norm(content)


def load_eval_set(path: Path = EVAL_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _accessible_chunks(db: Session, user_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = db.execute(
        text("SELECT c.id::text, c.content FROM document_chunks c JOIN documents d ON d.id = c.document_id "
             "WHERE d.status = 'indexed' AND (d.visibility = 'shared' OR d.owner_id = :uid)"),
        {"uid": user_id},
    ).all()
    return [(r[0], r[1]) for r in rows]


def _ndcg(relevances: list[int], ideal_count: int, k: int) -> float:
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevances[:k]))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, ideal_count)))
    return dcg / ideal if ideal > 0 else 0.0


def _evaluate_question(db: Session, settings: Settings, item: dict, mode: str, user_id: uuid.UUID,
                       corpus: list[tuple[str, str]], private_doc_id: str | None) -> dict:
    processed = query_processing.process(item["question"], item.get("previous_questions"))
    started = time.perf_counter()
    mentioned = [f.id for f in resolve_fund_mentions(db, processed.retrieval_text)]
    result = retrieve(db, settings, processed, Filters(user_id), mode=mode, mentioned_fund_ids=mentioned)
    latency = (time.perf_counter() - started) * 1000
    db.rollback()  # ends the read transaction (SET LOCAL etc.)
    snippets = item.get("snippets", [])
    ranked = result.candidates
    relevance = [int(any(_contains(c.content, s) for s in snippets)) for c in ranked]
    total_relevant = sum(1 for _, content in corpus if any(_contains(content, s) for s in snippets))
    record: dict = {
        "id": item["id"], "category": item["category"], "split": item.get("split", "dev"),
        "question": item["question"], "mode": mode,
        "retrieval_query": processed.retrieval_text, "latency_ms": round(latency, 1),
        "top_chunks": [
            {"rank": i + 1, "chunk_id": c.chunk_id, "document": c.document_title, "page": c.page_start,
             "relevant": bool(relevance[i]), "supported": c.supported,
             "vector_similarity": None if c.vector_similarity is None else round(c.vector_similarity, 3),
             "lexical_coverage": round(c.lexical_coverage, 3)}
            for i, c in enumerate(ranked[:5])
        ],
        "selected_count": len(result.selected),
        "conflict_detected": bool(result.conflicts),
    }
    if private_doc_id is not None:
        record["leaked_private_chunks"] = sum(1 for c in ranked if c.document_id == private_doc_id)
    if item.get("expect_no_evidence"):
        record["abstained"] = not result.selected
        return record
    for k in KS:
        top = ranked[:k]
        found = [s for s in snippets if any(_contains(c.content, s) for c in top)]
        record[f"recall@{k}"] = len(found) / len(snippets)
        record[f"precision@{k}"] = sum(relevance[:k]) / k
    first = next((i for i, rel in enumerate(relevance) if rel), None)
    record["reciprocal_rank"] = 0.0 if first is None else 1.0 / (first + 1)
    record["ndcg@5"] = _ndcg(relevance, total_relevant, 5)
    record["relevant_in_corpus"] = total_relevant
    selected_found = [s for s in snippets if any(_contains(c.content, s) for c in result.selected)]
    record["evidence_recall"] = len(selected_found) / len(snippets)
    record["false_abstention"] = not result.selected
    if mode == "hybrid":
        answer = generation.extractive_answer(item["question"], result.selected)
        check = citations.validate_citations(
            answer.text, {generation.source_marker(i) for i in range(len(result.selected))}, set())
        record["answer_mode"] = answer.mode
        record["invalid_citations"] = len(check.invalid_markers)
        expected = item.get("answer_must_contain", [])
        record["answer_contains_expected"] = all(e.lower() in answer.text.lower() for e in expected)
        if item.get("expect_conflict"):
            record["conflict_expected"] = True
    return record


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def _summarise(records: list[dict]) -> dict:
    answerable = [r for r in records if "reciprocal_rank" in r]
    negatives = [r for r in records if "abstained" in r]
    out: dict = {"questions": len(records), "answerable": len(answerable), "unanswerable": len(negatives)}
    for k in KS:
        out[f"recall@{k}"] = _mean([r[f"recall@{k}"] for r in answerable])
        out[f"precision@{k}"] = _mean([r[f"precision@{k}"] for r in answerable])
    out["mrr"] = _mean([r["reciprocal_rank"] for r in answerable])
    out["ndcg@5"] = _mean([r["ndcg@5"] for r in answerable])
    out["evidence_recall"] = _mean([r["evidence_recall"] for r in answerable])
    out["false_abstention_rate"] = _mean([float(r["false_abstention"]) for r in answerable])
    out["abstention_accuracy"] = _mean([float(r["abstained"]) for r in negatives])
    conflict = [r for r in records if r.get("conflict_expected")]
    if conflict:
        out["conflict_detection_rate"] = _mean([float(r["conflict_detected"]) for r in conflict])
    answers = [r for r in records if "answer_contains_expected" in r]
    if answers:
        out["answer_contains_expected_rate"] = _mean([float(r["answer_contains_expected"]) for r in answers])
        out["invalid_citations"] = sum(r["invalid_citations"] for r in answers)
    out["mean_latency_ms"] = _mean([r["latency_ms"] for r in records])
    by_category: dict[str, list[float]] = {}
    for r in answerable:
        by_category.setdefault(r["category"], []).append(r["recall@5"])
    out["recall@5_by_category"] = {k: _mean(v) for k, v in sorted(by_category.items())}
    return out


def run_evaluation(db: Session, settings: Settings, *, persist: bool = False,
                   eval_path: Path = EVAL_PATH) -> dict:
    spec = load_eval_set(eval_path)
    shared = db.scalar(select(Document.id).where(Document.visibility == "shared", Document.status == "indexed"))
    if shared is None:
        raise ValidationFailedError("No indexed shared documents. Run `python -m app.cli ingest-samples` first.")

    # Temporary users: one owns a private document, the other must never see it.
    tag = secrets.token_hex(4)
    owner = User(email=f"eval-owner-{tag}@eval.invalid", password_hash="!", display_name="Eval owner",
                 role="user", is_active=False, preferences={})
    outsider = User(email=f"eval-outsider-{tag}@eval.invalid", password_hash="!", display_name="Eval outsider",
                    role="user", is_active=False, preferences={})
    db.add_all([owner, outsider])
    db.commit()
    private = spec["private_document"]
    document = None
    try:
        document = indexing.create_document(
            db, settings, content=private["content"].encode("utf-8"), filename=private["filename"],
            owner_id=owner.id, visibility="private", title=private["title"], doc_type="research",
            fund_id=None, as_of_date=None,
        )
        indexing.index_document(db, settings, document)
        private_id = str(document.id)
        corpora = {owner.id: _accessible_chunks(db, owner.id), outsider.id: _accessible_chunks(db, outsider.id)}
        results: dict[str, list[dict]] = {}
        leaks = 0
        for mode in MODES:
            records = []
            for item in spec["questions"]:
                if item.get("permission_test"):
                    # Outsider: must not retrieve anything from the private note.
                    outside = _evaluate_question(db, settings, {**item, "snippets": [], "expect_no_evidence": True},
                                                 mode, outsider.id, corpora[outsider.id], private_id)
                    outside["id"] += "-outsider"
                    outside["category"] = "permission_outsider"
                    leaks += outside.get("leaked_private_chunks", 0)
                    records.append(outside)
                    inside = _evaluate_question(db, settings, item, mode, owner.id, corpora[owner.id], None)
                    inside["id"] += "-owner"
                    inside["category"] = "permission_owner"
                    records.append(inside)
                else:
                    records.append(_evaluate_question(db, settings, item, mode, outsider.id,
                                                      corpora[outsider.id], private_id))
            results[mode] = records
        summaries = {mode: _summarise(records) for mode, records in results.items()}
        summaries["hybrid"]["permission_leaks"] = leaks
        by_split = {}
        for split in ("dev", "holdout"):
            subset = [r for r in results["hybrid"] if r.get("split", "dev") == split]
            if subset:
                by_split[split] = _summarise(subset)
        summaries["hybrid_by_split"] = by_split
    finally:
        if document is not None:
            indexing.delete_document(db, settings, db.merge(document))
        db.execute(delete(User).where(User.id.in_([owner.id, outsider.id])))
        db.commit()

    acceptance = spec["acceptance"]
    hybrid = summaries[acceptance["mode"]]
    checks = {
        "recall@5": (hybrid["recall@5"], acceptance["recall_at_5_min"]),
        "mrr": (hybrid["mrr"], acceptance["mrr_min"]),
        "evidence_recall": (hybrid["evidence_recall"], acceptance["evidence_recall_min"]),
        "abstention_accuracy": (hybrid["abstention_accuracy"], acceptance["abstention_accuracy_min"]),
    }
    passed = all(value is not None and value >= minimum for value, minimum in checks.values())
    passed = passed and leaks <= acceptance["max_permission_leaks"]
    passed = passed and hybrid.get("invalid_citations", 0) <= acceptance["max_invalid_citations"]
    report = {
        "config": {
            "embedding": indexing.current_index_config(settings),
            "retrieval_candidates": settings.retrieval_candidates, "top_k": settings.retrieval_top_k,
            "rrf_k": settings.rrf_k, "rrf_vector_weight": settings.rrf_vector_weight,
            "rrf_lexical_weight": settings.rrf_lexical_weight,
            "answer_min_coverage": settings.answer_min_coverage,
            "answer_min_similarity": settings.answer_min_similarity,
            "include_min_coverage": settings.include_min_coverage,
            "include_coverage_ratio": settings.include_coverage_ratio,
            "include_similarity_margin": settings.include_similarity_margin,
            "context_word_budget": settings.context_word_budget,
            "max_chunks_per_document": settings.max_chunks_per_document, "reranker": settings.reranker,
        },
        "summary": {"passed": passed, "acceptance": acceptance, "modes": summaries,
                    "checks": {k: {"value": v, "minimum": m} for k, (v, m) in checks.items()}},
        "per_query": results,
    }
    if persist:
        db.add(RagEvaluationRun(config=report["config"], summary=report["summary"],
                                per_query=[r for records in results.values() for r in records]))
        db.commit()
    return report


def render_markdown(report: dict) -> str:
    modes = report["summary"]["modes"]
    metrics = ["recall@1", "recall@3", "recall@5", "precision@1", "precision@3", "precision@5", "mrr",
               "ndcg@5", "evidence_recall", "false_abstention_rate", "abstention_accuracy", "mean_latency_ms"]
    lines = ["| Metric (all questions) | " + " | ".join(MODES) + " |", "|---|" + "---|" * len(MODES)]
    for metric in metrics:
        row = []
        for mode in MODES:
            value = modes[mode].get(metric)
            row.append("n/a" if value is None else f"{value:.3f}" if metric != "mean_latency_ms" else f"{value:.1f}")
        lines.append(f"| {metric} | " + " | ".join(row) + " |")
    split_rows = modes.get("hybrid_by_split", {})
    if split_rows:
        lines += ["", "| Hybrid by split | questions | recall@5 | mrr | evidence_recall | abstention_accuracy |",
                  "|---|---|---|---|---|---|"]
        for split, summary in split_rows.items():
            lines.append(f"| {split} | {summary['questions']} | {summary['recall@5']} | {summary['mrr']} | "
                         f"{summary['evidence_recall']} | {summary['abstention_accuracy']} |")
    hybrid = modes["hybrid"]
    lines += [
        "",
        f"Hybrid: conflict detection rate {hybrid.get('conflict_detection_rate')}, "
        f"extractive answers containing the expected figure {hybrid.get('answer_contains_expected_rate')}, "
        f"invalid citations {hybrid.get('invalid_citations')}, permission leaks {hybrid.get('permission_leaks')}.",
        "",
        "Recall@5 by category (hybrid): " + ", ".join(f"{k} {v}" for k, v in hybrid["recall@5_by_category"].items()),
        "",
        f"Acceptance: {'PASSED' if report['summary']['passed'] else 'FAILED'} "
        + json.dumps(report["summary"]["checks"]),
    ]
    failures = [r for r in report["per_query"]["hybrid"]
                if r.get("recall@5", 1) < 1 or r.get("abstained") is False or r.get("evidence_recall", 1) < 1]
    if failures:
        lines += ["", "Hybrid questions with misses:"]
        for r in failures:
            lines.append(f"- {r['id']} ({r['category']}): {r['question']} "
                         f"recall@5={r.get('recall@5')} evidence_recall={r.get('evidence_recall')} "
                         f"abstained={r.get('abstained')}")
    return "\n".join(lines)
