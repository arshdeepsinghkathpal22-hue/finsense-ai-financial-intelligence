"""Document library (upload, indexing, search, re-index, delete) and the research assistant."""

import io

import pytest

from app.config import get_settings
from app.services.assistant import orchestrator
from tests.conftest import FAKE_LLM_KEY, FakeLLM

PRIVATE_NOTE = (
    "QUOKKA INCOME FUND - ANALYST NOTE\n\n"
    "Summary\n\n"
    "The Quokka Income Fund applies a lock-in of 17 days for every new purchase. "
    "Its analyst rating was raised to overweight in September 2026 after duration was cut to 2.1 years.\n"
)


def _upload(client, content: bytes, filename: str = "quokka_note.txt", **form):
    data = {"doc_type": "research", **form}
    return client.post("/api/v1/documents", data=data, files={"file": (filename, io.BytesIO(content), "text/plain")})


def test_sample_documents_are_indexed_and_shared(user_client):
    docs = user_client.get("/api/v1/documents").json()["items"]
    samples = [d for d in docs if d["is_synthetic"]]
    assert len(samples) == 4
    assert all(d["status"] == "indexed" and d["visibility"] == "shared" and d["chunk_count"] > 0 for d in samples)
    assert all(not d["can_manage"] for d in samples)  # regular users cannot manage the shared library
    assert all(d["index_config"]["embedding_dim"] == 256 for d in samples)


def test_upload_index_search_reindex_and_delete(user_client, other_client):
    uploaded = _upload(user_client, PRIVATE_NOTE.encode(), title="Quokka note")
    assert uploaded.status_code == 202
    doc_id = uploaded.json()["id"]
    detail = user_client.get(f"/api/v1/documents/{doc_id}").json()
    assert detail["status"] == "indexed" and detail["visibility"] == "private" and detail["chunk_count"] >= 1

    chunks = user_client.get(f"/api/v1/documents/{doc_id}/chunks").json()
    assert "lock-in of 17 days" in " ".join(c["content"] for c in chunks["items"])
    chunk_id = chunks["items"][0]["id"]
    assert user_client.get(f"/api/v1/documents/{doc_id}/chunks/{chunk_id}").status_code == 200

    found = user_client.post("/api/v1/documents/search", json={"query": "Quokka Income Fund lock-in"}).json()
    assert found["results"][0]["document_id"] == doc_id and found["evidence_found"]
    hidden = other_client.post("/api/v1/documents/search", json={"query": "Quokka Income Fund lock-in"}).json()
    assert all(r["document_id"] != doc_id for r in hidden["results"])

    download = user_client.get(f"/api/v1/documents/{doc_id}/file")
    assert download.status_code == 200 and download.content == PRIVATE_NOTE.encode()
    assert "attachment" in download.headers["content-disposition"]

    reindexed = user_client.post(f"/api/v1/documents/{doc_id}/reindex")
    assert reindexed.status_code == 202
    assert user_client.get(f"/api/v1/documents/{doc_id}").json()["status"] == "indexed"

    stored = list(get_settings().upload_dir.glob("*.txt"))
    assert user_client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
    assert user_client.get(f"/api/v1/documents/{doc_id}").status_code == 404
    assert len(list(get_settings().upload_dir.glob("*.txt"))) == len(stored) - 1  # file removed from disk
    after = user_client.post("/api/v1/documents/search", json={"query": "Quokka Income Fund lock-in"}).json()
    assert all(r["document_id"] != doc_id for r in after["results"])


def test_duplicate_upload_is_rejected(user_client):
    content = b"Duplicate detection note. The Wombat fund has no exit load at all.\n"
    assert _upload(user_client, content, "wombat.txt").status_code == 202
    again = _upload(user_client, content, "wombat-copy.txt")
    assert again.status_code == 409 and again.json()["error"]["details"]["document_id"]


@pytest.mark.parametrize("filename,content,fragment", [
    ("tool.exe", b"MZ\x90\x00", "Unsupported file type"),
    ("fake.pdf", b"just text pretending to be a PDF", "PDF signature"),
    ("cut.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "truncated"),
    ("binary.txt", b"abc\x00def", "binary data"),
    ("latin1.txt", "café crème".encode("latin-1"), "UTF-8"),
    ("empty.txt", b"", "empty"),
])
def test_malformed_uploads_are_rejected(user_client, filename, content, fragment):
    response = user_client.post("/api/v1/documents", data={"doc_type": "other"},
                                files={"file": (filename, io.BytesIO(content), "application/octet-stream")})
    assert response.status_code == 422
    assert fragment in response.json()["error"]["message"]


def test_oversized_upload_is_rejected(user_client):
    big = b"a" * (get_settings().max_upload_mb * 1024 * 1024 + 10)
    response = _upload(user_client, big, "big.txt")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_only_admins_can_add_shared_documents(user_client, admin_client):
    denied = _upload(user_client, b"Shared library attempt by a regular user.\n", "s.txt", visibility="shared")
    assert denied.status_code == 403
    allowed = _upload(admin_client, b"Admin shared note about the Numbat fund benchmark.\n", "n.txt",
                      visibility="shared")
    assert allowed.status_code == 202 and allowed.json()["visibility"] == "shared"
    doc_id = allowed.json()["id"]
    assert user_client.delete(f"/api/v1/documents/{doc_id}").status_code == 403
    assert admin_client.delete(f"/api/v1/documents/{doc_id}").status_code == 204


def test_scanned_and_corrupt_pdfs_get_clear_statuses(user_client):
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    image = io.BytesIO()
    Image.new("RGB", (300, 150), "white").save(image, format="PNG")
    pdf_buffer = io.BytesIO()
    pdf = canvas.Canvas(pdf_buffer, pagesize=A4)
    pdf.drawImage(ImageReader(io.BytesIO(image.getvalue())), 50, 500, width=300, height=150)
    pdf.save()
    scanned = user_client.post("/api/v1/documents", data={"doc_type": "factsheet"},
                               files={"file": ("scan.pdf", io.BytesIO(pdf_buffer.getvalue()), "application/pdf")})
    assert user_client.get(f"/api/v1/documents/{scanned.json()['id']}").json()["status"] == "needs_ocr"

    corrupt = user_client.post("/api/v1/documents", data={"doc_type": "factsheet"},
                               files={"file": ("bad.pdf", io.BytesIO(b"%PDF-1.4\ngarbage bytes\n%%EOF"),
                                               "application/pdf")})
    status = user_client.get(f"/api/v1/documents/{corrupt.json()['id']}").json()
    assert status["status"] == "failed" and status["error_message"]


def test_assistant_answers_document_question_with_citations(user_client):
    result = user_client.post("/api/v1/assistant/query", json={"question": "What is the exit load of FS-DB-007?"}).json()
    assert result["mode"] == "extractive"
    assert "0.25%" in result["answer"] and "[S1]" in result["answer"]
    assert result["sources"][0]["document_title"] and result["sources"][0]["page_start"] >= 1
    assert any("SYNTHETIC" in note for note in result["limitations"])


def test_assistant_runs_analytics_tools(user_client):
    result = user_client.post("/api/v1/assistant/query",
                              json={"question": "What is the 3-year Sharpe ratio of Aurora Bluechip?"}).json()
    calc = next(c for c in result["calculations"] if c["tool"] == "fund_metrics")
    assert calc["marker"] == "T1" and any("Sharpe" in line for line in calc["lines"])
    assert "[T1]" in result["answer"]


def test_assistant_abstains_without_evidence(user_client):
    result = user_client.post("/api/v1/assistant/query", json={"question": "Who is the CEO of Tesla?"}).json()
    assert result["mode"] == "insufficient_evidence" and result["sources"] == []


def test_assistant_flags_conflicting_periods(user_client):
    result = user_client.post("/api/v1/assistant/query", json={
        "question": "What are the assets under management of Aurora Bluechip Equity Fund?"}).json()
    assert result["conflicts"] and len(result["conflicts"][0]["periods"]) == 2
    assert "crore" in result["answer"]


def test_conversation_follow_up_and_ownership(user_client, other_client):
    first = user_client.post("/api/v1/assistant/query",
                             json={"question": "Tell me about the Meridian Dynamic Bond Fund exit load"}).json()
    convo = first["conversation_id"]
    follow = user_client.post("/api/v1/assistant/query",
                              json={"question": "What is its expense ratio?", "conversation_id": convo}).json()
    assert follow["retrieval"]["is_follow_up"] and follow["conversation_id"] == convo
    history = user_client.get(f"/api/v1/assistant/conversations/{convo}").json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
    assert other_client.get(f"/api/v1/assistant/conversations/{convo}").status_code == 404
    hijack = other_client.post("/api/v1/assistant/query", json={"question": "And the outlook?", "conversation_id": convo})
    assert hijack.status_code == 404
    assert user_client.delete(f"/api/v1/assistant/conversations/{convo}").status_code == 204
    assert user_client.get(f"/api/v1/assistant/conversations/{convo}").status_code == 404


def test_assistant_with_language_model_validates_citations(user_client, monkeypatch):
    llm = FakeLLM(reply="The exit load is 0.25% if redeemed within 30 days [S1]. It is also 9.99% [S7].")
    monkeypatch.setattr(orchestrator, "get_llm_client", lambda settings: llm)
    result = user_client.post("/api/v1/assistant/query", json={"question": "What is the exit load of FS-DB-007?"}).json()
    assert result["mode"] == "llm" and result["model"] == "fake/test-model"
    assert "[S7]" not in result["answer"] and "[S1]" in result["answer"]
    assert any("S7" in w for w in result["warnings"])  # forged marker reported
    assert any("9.99%" in w for w in result["warnings"])  # number not found in evidence
    assert next(s for s in result["sources"] if s["marker"] == "S1")["cited"]
    for system, messages in llm.calls:
        prompt = system + "".join(m.content for m in messages)
        assert FAKE_LLM_KEY not in prompt


def test_assistant_uses_llm_planner_when_valid(user_client, monkeypatch):
    llm = FakeLLM(plan='{"tools": [{"name": "compare_funds", "args": {"funds": ["FS-LC-001", "FS-MC-002"], '
                       '"period": "1y"}}], "use_documents": false}',
                  reply="Northstar was more volatile than Aurora [T1].")
    monkeypatch.setattr(orchestrator, "get_llm_client", lambda settings: llm)
    result = user_client.post("/api/v1/assistant/query",
                              json={"question": "Compare Aurora Bluechip and Northstar Midcap over 1 year"}).json()
    assert result["planner"] == "llm"
    assert result["calculations"][0]["tool"] == "compare_funds"
