"""Authentication, CSRF and per-user isolation of portfolios, documents and conversations."""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditEvent, User, UserSession
from tests.conftest import PASSWORD, ApiClient

PROTECTED = [
    ("get", "/api/v1/auth/me"),
    ("get", "/api/v1/funds"),
    ("get", "/api/v1/funds/1/risk"),
    ("get", "/api/v1/dashboard"),
    ("get", "/api/v1/portfolios"),
    ("post", "/api/v1/portfolios/optimize"),
    ("get", "/api/v1/documents"),
    ("post", "/api/v1/documents/search"),
    ("post", "/api/v1/assistant/query"),
    ("get", "/api/v1/assistant/conversations"),
    ("post", "/api/v1/ml/forecast"),
    ("post", "/api/v1/simulate/sip"),
    ("get", "/api/v1/system/status"),
    ("get", "/api/v1/admin/data-status"),
]


@pytest.mark.parametrize("method,url", PROTECTED)
def test_protected_endpoints_require_a_session(anon_client, method, url):
    response = getattr(anon_client, method)(url, **({"json": {}} if method == "post" else {}))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_forged_session_cookie_is_rejected(app):
    client = TestClient(app, cookies={"fs_session": "forged-token-value"})
    assert client.get("/api/v1/auth/me").status_code == 401


def test_state_changing_requests_need_the_csrf_token(user_client):
    no_header = user_client.client.post("/api/v1/portfolios", json={"name": "x", "assets": []})
    assert no_header.status_code == 403
    wrong = user_client.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "not-the-token"})
    assert wrong.status_code == 403
    assert user_client.get("/api/v1/auth/me").status_code == 200  # the session survived


@pytest.mark.parametrize("method,url", [
    ("get", "/api/v1/admin/data-status"),
    ("get", "/api/v1/admin/users"),
    ("get", "/api/v1/admin/audit"),
    ("get", "/api/v1/admin/imports"),
    ("post", "/api/v1/admin/rag/diagnostics"),
    ("post", "/api/v1/admin/rag/evaluate"),
    ("post", "/api/v1/admin/documents/reindex-outdated"),
    ("post", "/api/v1/admin/amfi/sync"),
])
def test_admin_endpoints_reject_regular_users(user_client, method, url):
    kwargs = {"json": {"query": "anything"}} if method == "post" else {}
    response = getattr(user_client, method)(url, **kwargs)
    assert response.status_code == 403


def test_users_cannot_promote_themselves(user_client):
    response = user_client.patch("/api/v1/auth/me", json={"role": "admin", "display_name": "Me"})
    assert response.status_code in (200, 422)
    assert user_client.get("/api/v1/auth/me").json()["role"] == "user"


def test_portfolios_are_private_to_their_owner(user_client, other_client, fund_ids):
    created = user_client.post("/api/v1/portfolios", json={
        "name": "Private book", "initial_value": 10000,
        "assets": [{"fund_id": fund_ids["FS-LC-001"], "weight_pct": 100}]}).json()
    pid = created["id"]
    body = {"name": "Stolen", "initial_value": 1, "assets": [{"fund_id": fund_ids["FS-LC-001"], "weight_pct": 100}]}
    attempts = [
        other_client.get(f"/api/v1/portfolios/{pid}"),
        other_client.get(f"/api/v1/portfolios/{pid}/analytics"),
        other_client.get(f"/api/v1/portfolios/{pid}/export.csv"),
        other_client.post(f"/api/v1/portfolios/{pid}/optimize", json={}),
        other_client.put(f"/api/v1/portfolios/{pid}", json=body),
        other_client.put(f"/api/v1/portfolios/{pid}/weights", json={"assets": body["assets"]}),
        other_client.delete(f"/api/v1/portfolios/{pid}"),
        other_client.get("/api/v1/dashboard", params={"portfolio_id": pid}),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert all(p["id"] != pid for p in other_client.get("/api/v1/portfolios").json()["items"])
    assert user_client.get(f"/api/v1/portfolios/{pid}").json()["name"] == "Private book"


def test_assistant_tools_cannot_reach_another_users_portfolio(user_client, other_client, fund_ids):
    user_client.post("/api/v1/portfolios", json={
        "name": "Secret Sauce", "initial_value": 10000,
        "assets": [{"fund_id": fund_ids["FS-LC-001"], "weight_pct": 100}]})
    result = other_client.post("/api/v1/assistant/query",
                               json={"question": "Summarise my portfolio Secret Sauce"}).json()
    assert all(c["tool"] not in ("portfolio_summary", "optimise_portfolio") for c in result["calculations"])
    assert "Secret Sauce" not in str(result["calculations"])


def test_private_documents_are_isolated_everywhere(user_client, other_client):
    content = b"PELICAN NOTE\n\nThe Pelican strategy targets 41% in gilts. This note is private.\n"
    doc_id = user_client.post("/api/v1/documents", data={"doc_type": "research"},
                              files={"file": ("pelican.txt", io.BytesIO(content), "text/plain")}).json()["id"]
    chunk_id = user_client.get(f"/api/v1/documents/{doc_id}/chunks").json()["items"][0]["id"]
    attempts = [
        other_client.get(f"/api/v1/documents/{doc_id}"),
        other_client.get(f"/api/v1/documents/{doc_id}/chunks"),
        other_client.get(f"/api/v1/documents/{doc_id}/chunks/{chunk_id}"),
        other_client.get(f"/api/v1/documents/{doc_id}/file"),
        other_client.post(f"/api/v1/documents/{doc_id}/reindex"),
        other_client.delete(f"/api/v1/documents/{doc_id}"),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert all(d["id"] != doc_id for d in other_client.get("/api/v1/documents").json()["items"])

    question = {"question": "What does the Pelican strategy target in gilts?"}
    own = user_client.post("/api/v1/assistant/query", json=question).json()
    assert any(s["document_id"] == doc_id for s in own["sources"]) and "41%" in own["answer"]
    leaked = other_client.post("/api/v1/assistant/query", json=question).json()
    assert all(s["document_id"] != doc_id for s in leaked["sources"]) and "41%" not in leaked["answer"]
    # Explicitly naming the document id must not bypass the access filter either.
    forced = other_client.post("/api/v1/assistant/query", json={**question, "document_ids": [doc_id]}).json()
    assert forced["sources"] == [] and "41%" not in forced["answer"]
    searched = other_client.post("/api/v1/documents/search",
                                 json={"query": "Pelican strategy gilts", "document_ids": [doc_id]}).json()
    assert searched["results"] == []


def test_admin_diagnostics_do_not_expose_private_documents(user_client, admin_client):
    content = b"HERON NOTE\n\nThe Heron model portfolio holds 23% in floating rate bonds.\n"
    user_client.post("/api/v1/documents", data={"doc_type": "research"},
                     files={"file": ("heron.txt", io.BytesIO(content), "text/plain")})
    diag = admin_client.post("/api/v1/admin/rag/diagnostics", json={"query": "Heron model floating rate bonds"}).json()
    assert "Heron" not in " ".join(c["excerpt"] for c in diag["candidates"])


def test_sessions_and_passwords_are_stored_hashed(app, db):
    client = ApiClient(TestClient(app))
    email = "hash-check@example.com"
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": "H"})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    raw_token = login.cookies.get("fs_session") or client.client.cookies.get("fs_session")
    user = db.scalar(select(User).where(User.email == email))
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash
    stored = db.scalars(select(UserSession.token_hash).where(UserSession.user_id == user.id)).all()
    assert stored and raw_token not in stored and all(len(h) == 64 for h in stored)


def test_failed_logins_are_audited_without_the_password(anon_client, db):
    anon_client.post("/api/v1/auth/login", json={"email": "audit-me@example.com", "password": "Guess-Pass-999"})
    events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "auth.login_failed")).all()
    assert any(e.details.get("email") == "audit-me@example.com" for e in events)
    assert all("Guess-Pass-999" not in str(e.details) for e in events)
