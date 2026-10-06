"""The labelled retrieval evaluation must keep meeting its acceptance thresholds."""

from app.config import get_settings
from app.services.rag import evaluation


def test_rag_evaluation_meets_acceptance_thresholds(db):
    report = evaluation.run_evaluation(db, get_settings(), persist=False)
    summary = report["summary"]
    assert summary["passed"], summary["checks"]
    hybrid = summary["modes"]["hybrid"]
    assert hybrid["permission_leaks"] == 0 and hybrid["invalid_citations"] == 0
    assert hybrid["abstention_accuracy"] == 1.0
    # Hybrid retrieval must keep its recall advantage over either retriever alone.
    assert hybrid["recall@5"] >= summary["modes"]["lexical"]["recall@5"]
    assert hybrid["recall@5"] > summary["modes"]["vector"]["recall@5"]
    markdown = evaluation.render_markdown(report)
    assert "Acceptance: PASSED" in markdown
