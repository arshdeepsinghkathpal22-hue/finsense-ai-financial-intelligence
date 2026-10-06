"""End-to-end acceptance workflow, driven only through the public HTTP API.

By default it runs in-process against the test database (FastAPI TestClient).
Set ``FINSENSE_E2E_BASE_URL`` (for example ``http://localhost:8080`` for the
Docker deployment, or ``http://127.0.0.1:8000``) to run the same workflow
against a live server; the server must have the demo data seeded.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import E2E_BASE_URL, ApiClient

PASSWORD = "E2e-Workflow-Pass-1"
NOTE = (
    "OSPREY GILT FUND - INVESTMENT COMMITTEE NOTE\n\n"
    "Decision\n\n"
    "The committee set the Osprey Gilt Fund's target modified duration at 6.4 years for the next quarter. "
    "The fund keeps at least 92% of assets in government securities.\n"
)


@pytest.fixture(scope="module")
def new_client(request):
    if E2E_BASE_URL:
        return lambda: ApiClient(httpx.Client(base_url=E2E_BASE_URL, timeout=180))
    app = request.getfixturevalue("app")
    return lambda: ApiClient(TestClient(app))


def _sign_up(client: ApiClient) -> dict:
    email = f"e2e-{uuid.uuid4().hex[:12]}@example.com"
    registered = client.post("/api/v1/auth/register",
                             json={"email": email, "password": PASSWORD, "display_name": "E2E Analyst"})
    assert registered.status_code == 201, registered.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.csrf = login.json()["csrf_token"]
    client.user = login.json()["user"]
    return client.user


def _ok(response, status: int = 200) -> dict:
    assert response.status_code == status, f"{response.request.method} {response.request.url}: {response.text[:500]}"
    return response.json() if response.content else {}


def test_full_user_workflow(new_client):
    client = new_client()

    # 1. Service is up and the database is reachable.
    assert _ok(client.get("/api/v1/health/ready"))["database"] == "ok"

    # 2. Register and sign in.
    user = _sign_up(client)
    assert _ok(client.get("/api/v1/auth/me"))["email"] == user["email"]

    # 3. Dashboard and fund discovery.
    dashboard = _ok(client.get("/api/v1/dashboard"))
    assert dashboard["selection"]["kind"] == "fund"
    funds = {f["scheme_code"]: f["id"] for f in _ok(client.get("/api/v1/funds", params={"page_size": 100}))["items"]}
    assert {"FS-LC-001", "FS-MC-002", "FS-DB-007", "FS-LIQ-008"} <= set(funds)

    # 4. Fund risk report and fund comparison.
    risk = _ok(client.get(f"/api/v1/funds/{funds['FS-LC-001']}/risk", params={"period": "3y"}))
    assert risk["metrics"]["sharpe_ratio"]["value"] is not None and risk["fund"]["is_synthetic"]
    compare = _ok(client.get("/api/v1/funds/compare", params={"ids": f"{funds['FS-LC-001']},{funds['FS-MC-002']}"}))
    assert len(compare["funds"]) == 2

    # 5. Build a portfolio.
    portfolio = _ok(client.post("/api/v1/portfolios", json={
        "name": "E2E core", "initial_value": 250000,
        "assets": [{"fund_id": funds["FS-LC-001"], "weight_pct": 40}, {"fund_id": funds["FS-MC-002"], "weight_pct": 20},
                   {"fund_id": funds["FS-DB-007"], "weight_pct": 30}, {"fund_id": funds["FS-LIQ-008"], "weight_pct": 10}]}),
        201)
    pid = portfolio["id"]

    # 6. Portfolio analytics.
    analytics = _ok(client.get(f"/api/v1/portfolios/{pid}/analytics"))
    assert analytics["metrics"]["volatility"]["value"] > 0 and analytics["value"]["start"] == 250000

    # 7. Optimise, then apply the optimised weights.
    optimised = _ok(client.post(f"/api/v1/portfolios/{pid}/optimize", json={"risk_profile": "conservative",
                                                                           "max_weight": 0.5}))
    new_weights = optimised["comparison"]["optimised"]["weights"]
    fund_order = optimised["assets"]
    assets = [{"fund_id": f["fund_id"], "weight_pct": round(w * 100, 4)}
              for f, w in zip(fund_order, new_weights, strict=True) if w > 1e-6]
    drift = 100 - sum(a["weight_pct"] for a in assets)
    assets[0]["weight_pct"] = round(assets[0]["weight_pct"] + drift, 4)
    applied = _ok(client.put(f"/api/v1/portfolios/{pid}/weights", json={"assets": assets}))
    assert abs(sum(a["weight_pct"] for a in applied["assets"]) - 100) < 1e-3

    # 8. What-if simulation.
    shock = _ok(client.post("/api/v1/simulate/market-shock", json={
        "fund_ids": [funds["FS-LC-001"], funds["FS-DB-007"]], "weights_pct": [60, 40], "market_move": -0.15}))
    assert shock["estimated_portfolio_return"] < 0
    sip = _ok(client.post("/api/v1/simulate/sip", json={"monthly_amount": 5000, "years": 5, "annual_return": 0.1}))
    assert sip["final_value"] > sip["total_invested"]

    # 9. Machine-learning forecast with baseline comparison and explanation.
    forecast = _ok(client.post("/api/v1/ml/forecast", json={"fund_id": funds["FS-LC-001"], "horizon_days": 21,
                                                            "model_type": "random_forest"}))
    assert "beats_baseline" in forecast["evaluation"] and forecast["explanation"]["available"]

    # 10. Upload a private document and wait for indexing.
    upload = client.post("/api/v1/documents", data={"doc_type": "research", "title": "Osprey committee note"},
                         files={"file": ("osprey_note.txt", NOTE.encode(), "text/plain")})
    doc_id = _ok(upload, 202)["id"]
    deadline = time.monotonic() + 180
    status = "pending"
    while time.monotonic() < deadline:
        status = _ok(client.get(f"/api/v1/documents/{doc_id}"))["status"]
        if status not in ("pending", "processing"):
            break
        time.sleep(1)
    assert status == "indexed"

    # 11. Ask the assistant: a document question (cited) and an analytics question.
    doc_answer = _ok(client.post("/api/v1/assistant/query",
                                 json={"question": "What target modified duration did the committee set for the Osprey Gilt Fund?"}))
    assert "6.4 years" in doc_answer["answer"]
    assert any(s["document_id"] == doc_id and s["cited"] for s in doc_answer["sources"])
    calc_answer = _ok(client.post("/api/v1/assistant/query",
                                  json={"question": "What is the maximum drawdown of Northstar Midcap over 3 years?",
                                        "conversation_id": doc_answer["conversation_id"]}))
    assert calc_answer["calculations"] and "[T1]" in calc_answer["answer"]
    abstain = _ok(client.post("/api/v1/assistant/query", json={"question": "What is the price of gold per gram today?"}))
    assert abstain["mode"] == "insufficient_evidence"

    # 12. Another user cannot see any of it; then clean up and sign out.
    intruder = new_client()
    _sign_up(intruder)
    assert intruder.get(f"/api/v1/portfolios/{pid}").status_code == 404
    assert intruder.get(f"/api/v1/documents/{doc_id}").status_code == 404
    leaked = _ok(intruder.post("/api/v1/assistant/query",
                               json={"question": "What target modified duration did the committee set for the Osprey Gilt Fund?"}))
    assert "6.4 years" not in leaked["answer"] and all(s["document_id"] != doc_id for s in leaked["sources"])

    assert client.get(f"/api/v1/portfolios/{pid}/export.csv").status_code == 200
    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
    assert client.delete(f"/api/v1/portfolios/{pid}").status_code == 204
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
