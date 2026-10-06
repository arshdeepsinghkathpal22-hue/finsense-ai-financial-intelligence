"""Captures example API responses into docs/api-samples/ from a running server.

Usage (with the stack running and the demo data loaded):

    python scripts/capture_api_samples.py --base-url http://localhost:8080

A throwaway user is registered for the capture. Administrator samples are
included only when FINSENSE_ADMIN_EMAIL and FINSENSE_ADMIN_PASSWORD are set.
Long arrays (time series) are shortened so the files stay readable; every
shortened list says how many items it originally had. Requires `httpx`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "docs" / "api-samples"
KEEP = 4


def shorten(value):  # noqa: ANN001, ANN201
    if isinstance(value, list):
        items = [shorten(v) for v in value[:KEEP]]
        if len(value) > KEEP:
            items.append(f"... {len(value) - KEEP} more items (total {len(value)})")
        return items
    if isinstance(value, dict):
        return {k: shorten(v) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 600:
        return value[:600] + f"... [{len(value)} characters]"
    return value


class Session:
    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=180)
        self.csrf: str | None = None

    def login(self, email: str, password: str) -> httpx.Response:
        response = self.http.post("/api/v1/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        self.csrf = response.json()["csrf_token"]
        return response

    def call(self, method: str, url: str, **kwargs) -> httpx.Response:  # noqa: ANN003
        headers = kwargs.pop("headers", {})
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return self.http.request(method, url, headers=headers, **kwargs)


def save(name: str, response: httpx.Response, request: dict | None = None) -> None:
    body = response.json() if response.content else None
    record = {"request": request, "status": response.status_code, "body": shorten(body)}
    (OUT / f"{name}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{response.status_code}  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    anon = Session(args.base_url)
    save("error_unauthenticated", anon.call("GET", "/api/v1/funds"), {"method": "GET", "url": "/api/v1/funds"})
    email, password = f"sample-{uuid.uuid4().hex[:8]}@example.com", "Sample-Capture-Pass-1"
    user = Session(args.base_url)
    user.call("POST", "/api/v1/auth/register", json={"email": email, "password": password, "display_name": "Sample"})
    save("auth_login", user.login(email, password), {"method": "POST", "url": "/api/v1/auth/login"})

    def get(name: str, url: str, **params) -> httpx.Response:  # noqa: ANN003
        response = user.call("GET", url, params=params or None)
        save(name, response, {"method": "GET", "url": url, "params": params or None})
        return response

    def post(name: str, url: str, body: dict) -> httpx.Response:
        response = user.call("POST", url, json=body)
        save(name, response, {"method": "POST", "url": url, "json": body})
        return response

    get("auth_me", "/api/v1/auth/me")
    get("system_status", "/api/v1/system/status")
    funds = {f["scheme_code"]: f["id"] for f in get("funds_list", "/api/v1/funds").json()["items"]}
    lc, mc, db, liq = funds["FS-LC-001"], funds["FS-MC-002"], funds["FS-DB-007"], funds["FS-LIQ-008"]
    get("funds_categories", "/api/v1/funds/categories")
    get("fund_detail", f"/api/v1/funds/{lc}")
    get("fund_risk", f"/api/v1/funds/{lc}/risk", period="3y")
    get("fund_risk_short_history", f"/api/v1/funds/{funds['FS-SC-003']}/risk", period="5y")
    get("fund_nav", f"/api/v1/funds/{lc}/nav", period="1y")
    get("fund_holdings", f"/api/v1/funds/{lc}/holdings")
    get("fund_aum", f"/api/v1/funds/{lc}/aum")
    get("fund_sip", f"/api/v1/funds/{lc}/sip")
    get("funds_compare", "/api/v1/funds/compare", ids=f"{lc},{mc}", period="3y")
    get("benchmarks", "/api/v1/benchmarks")
    post("correlation", "/api/v1/analytics/correlation", {"fund_ids": [lc, mc, db, liq]})
    get("dashboard_default", "/api/v1/dashboard")

    created = post("portfolio_create", "/api/v1/portfolios", {
        "name": "Sample core", "initial_value": 100000,
        "assets": [{"fund_id": lc, "weight_pct": 40}, {"fund_id": mc, "weight_pct": 20},
                   {"fund_id": db, "weight_pct": 25}, {"fund_id": liq, "weight_pct": 15}]}).json()
    pid = created["id"]
    get("portfolios_list", "/api/v1/portfolios")
    get("portfolio_analytics", f"/api/v1/portfolios/{pid}/analytics")
    post("portfolio_optimize", f"/api/v1/portfolios/{pid}/optimize", {"risk_profile": "moderate", "max_weight": 0.5})
    post("portfolio_optimize_infeasible", f"/api/v1/portfolios/{pid}/optimize", {"max_weight": 0.2})
    post("portfolio_optimize_adhoc", "/api/v1/portfolios/optimize",
         {"fund_ids": [lc, db], "current_weights_pct": [50, 50], "objective": "min_variance"})
    get("dashboard_portfolio", "/api/v1/dashboard", portfolio_id=pid)

    post("sim_sip", "/api/v1/simulate/sip", {"monthly_amount": 10000, "years": 10, "annual_return": 0.11})
    post("sim_monte_carlo", "/api/v1/simulate/monte-carlo", {"monthly_amount": 10000, "years": 10,
                                                             "annual_return": 0.11, "annual_volatility": 0.16})
    post("sim_market_shock", "/api/v1/simulate/market-shock",
         {"fund_ids": [lc, db], "weights_pct": [60, 40], "market_move": -0.2})
    post("sim_stress", "/api/v1/simulate/stress", {"fund_ids": [lc, db], "weights_pct": [60, 40],
                                                    "volatility_multiplier": 2})
    post("sim_allocation_change", "/api/v1/simulate/allocation-change",
         {"fund_ids": [lc, db], "current_weights_pct": [60, 40], "proposed_weights_pct": [40, 60]})

    get("ml_capabilities", "/api/v1/ml/capabilities")
    post("ml_forecast", "/api/v1/ml/forecast", {"fund_id": lc, "horizon_days": 21, "model_type": "random_forest"})
    post("ml_anomalies", "/api/v1/ml/anomalies", {"fund_id": mc, "contamination": 0.01})
    get("ml_models", "/api/v1/ml/models", fund_id=lc)

    docs = get("documents_list", "/api/v1/documents").json()["items"]
    sid = next(d for d in docs if d["doc_type"] == "sid")
    get("document_detail", f"/api/v1/documents/{sid['id']}")
    get("document_chunks", f"/api/v1/documents/{sid['id']}/chunks", page_size=3)
    post("documents_search", "/api/v1/documents/search", {"query": "FS-DB-007 exit load", "top_k": 3})
    post("assistant_query_docs", "/api/v1/assistant/query", {"question": "What is the exit load of FS-DB-007?"})
    post("assistant_query_analytics", "/api/v1/assistant/query",
         {"question": "Compare Aurora Bluechip and Northstar Midcap over 3 years"})
    post("assistant_query_conflict", "/api/v1/assistant/query",
         {"question": "What are the assets under management of Aurora Bluechip Equity Fund?"})
    post("assistant_query_insufficient", "/api/v1/assistant/query", {"question": "What is the price of gold today?"})
    get("assistant_conversations", "/api/v1/assistant/conversations")
    post("error_validation", "/api/v1/simulate/sip", {"monthly_amount": -5, "years": 0, "annual_return": 0.1})
    user.call("DELETE", f"/api/v1/portfolios/{pid}")

    admin_email, admin_password = os.environ.get("FINSENSE_ADMIN_EMAIL"), os.environ.get("FINSENSE_ADMIN_PASSWORD")
    if admin_email and admin_password:
        admin = Session(args.base_url)
        admin.login(admin_email, admin_password)
        admin_pages = (("admin_data_status", "/api/v1/admin/data-status"), ("admin_imports", "/api/v1/admin/imports"),
                       ("admin_audit", "/api/v1/admin/audit"), ("admin_users", "/api/v1/admin/users"))
        for name, url in admin_pages:
            save(name, admin.call("GET", url, params={"page_size": 5} if "audit" in url else None),
                 {"method": "GET", "url": url})
        body = {"query": "FS-DB-007 exit load"}
        save("admin_rag_diagnostics", admin.call("POST", "/api/v1/admin/rag/diagnostics", json=body),
             {"method": "POST", "url": "/api/v1/admin/rag/diagnostics", "json": body})
    else:
        print("FINSENSE_ADMIN_EMAIL / FINSENSE_ADMIN_PASSWORD not set: administrator samples skipped.")


if __name__ == "__main__":
    main()
