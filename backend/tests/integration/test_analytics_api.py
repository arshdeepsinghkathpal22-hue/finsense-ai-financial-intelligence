"""Funds, analytics, dashboard, portfolios, simulations and ML endpoints."""

import io

import pandas as pd
import pytest

from app.services.analytics import metrics, series


def test_fund_list_search_and_filters(user_client):
    all_funds = user_client.get("/api/v1/funds").json()
    assert all_funds["total"] == 10
    assert all(f["is_synthetic"] for f in all_funds["items"])
    found = user_client.get("/api/v1/funds", params={"q": "aurora"}).json()
    assert [f["scheme_code"] for f in found["items"]] == ["FS-LC-001"]
    debt = user_client.get("/api/v1/funds", params={"asset_class": "debt"}).json()
    assert {f["scheme_code"] for f in debt["items"]} == {"FS-DB-007", "FS-LIQ-008"}
    paged = user_client.get("/api/v1/funds", params={"page_size": 3, "page": 2}).json()
    assert len(paged["items"]) == 3 and paged["page"] == 2


def test_fund_detail_and_missing_data_labels(user_client, fund_ids):
    detail = user_client.get(f"/api/v1/funds/{fund_ids['FS-LC-001']}").json()
    assert detail["aum"]["value_crore"] > 0 and detail["holdings_dates"] == ["2026-03-31", "2025-09-30"]
    assert {r["period"] for r in detail["trailing_returns"]} >= {"1y", "3y", "since_start"}
    small = user_client.get(f"/api/v1/funds/{fund_ids['FS-SC-003']}").json()
    five_year = next(r for r in small["trailing_returns"] if r["period"] == "5y")
    assert five_year["value"] is None and five_year["unavailable_reason"]
    idcw = user_client.get(f"/api/v1/funds/{fund_ids['FS-FX-004D']}").json()
    assert any("IDCW" in note for note in idcw["data_notes"])
    assert user_client.get("/api/v1/funds/999999").status_code == 404


def test_risk_report_matches_direct_calculation(user_client, fund_ids, db):
    fid = fund_ids["FS-LC-001"]
    report = user_client.get(f"/api/v1/funds/{fid}/risk", params={"period": "3y"}).json()
    nav = series.load_fund_nav(db, fid)
    window = nav.loc[pd.Timestamp(report["period"]["start"]):]
    returns = metrics.simple_returns(window)
    assert report["metrics"]["volatility"]["value"] == pytest.approx(metrics.annualized_volatility(returns, 252))
    assert report["metrics"]["cumulative_return"]["value"] == pytest.approx(metrics.cumulative_return(window))
    assert report["metrics"]["historical_var"]["confidence"] == 0.95
    assert report["assumptions"]["risk_free_rate_annual"] == 0.065
    assert "linear interpolation" in report["assumptions"]["var_method"]
    for key in ("rolling_sharpe", "drawdown", "benchmark_rebased", "return_distribution"):
        assert report["series"][key]


def test_risk_report_respects_user_risk_free_rate(user_client, fund_ids):
    fid = fund_ids["FS-LC-001"]
    base = user_client.get(f"/api/v1/funds/{fid}/risk").json()["metrics"]["sharpe_ratio"]["value"]
    custom = user_client.get(f"/api/v1/funds/{fid}/risk", params={"risk_free_rate": 0.0}).json()
    assert custom["assumptions"]["risk_free_rate_annual"] == 0.0
    assert custom["metrics"]["sharpe_ratio"]["value"] > base


def test_holdings_aum_sip_and_compare(user_client, fund_ids):
    fid = fund_ids["FS-LC-001"]
    holdings = user_client.get(f"/api/v1/funds/{fid}/holdings", params={"as_of": "2026-03-31"}).json()
    assert holdings["concentration"]["top10_weight_pct"] == pytest.approx(51.8)
    assert holdings["total_weight_pct"] == pytest.approx(100.0)
    assert user_client.get(f"/api/v1/funds/{fid}/aum").json()["points"]
    no_sip = user_client.get(f"/api/v1/funds/{fund_ids['FS-DB-007']}/sip").json()
    assert no_sip["points"] == [] and no_sip["note"]
    compare = user_client.get("/api/v1/funds/compare", params={"ids": f"{fid},{fund_ids['FS-MC-002']}"}).json()
    assert len(compare["funds"]) == 2 and compare["chart"]["points"][0][str(fid)] == pytest.approx(100.0)
    assert user_client.get("/api/v1/funds/compare", params={"ids": str(fid)}).status_code == 422


def test_correlation_matrix(user_client, fund_ids):
    ids = [fund_ids["FS-LC-001"], fund_ids["FS-IDX-006"], fund_ids["FS-LIQ-008"]]
    result = user_client.post("/api/v1/analytics/correlation", json={"fund_ids": ids}).json()
    matrix = result["matrix"]
    assert matrix[0][0] == 1.0 and matrix[0][1] > 0.9 and abs(matrix[0][2]) < 0.2


def _create_portfolio(client, fund_ids, name="Core", weights=None):
    weights = weights or {"FS-LC-001": 40, "FS-MC-002": 20, "FS-DB-007": 25, "FS-LIQ-008": 15}
    return client.post("/api/v1/portfolios", json={
        "name": name, "initial_value": 100000,
        "assets": [{"fund_id": fund_ids[c], "weight_pct": w} for c, w in weights.items()]})


def test_portfolio_lifecycle(user_client, fund_ids):
    created = _create_portfolio(user_client, fund_ids)
    assert created.status_code == 201
    pid = created.json()["id"]
    assert _create_portfolio(user_client, fund_ids).status_code == 409  # unique name per user
    analytics = user_client.get(f"/api/v1/portfolios/{pid}/analytics").json()
    holdings = analytics["allocation"]["holdings"]
    assert sum(h["current_weight"] for h in holdings) == pytest.approx(1.0)
    assert sum(h["risk_contribution"] for h in holdings) == pytest.approx(1.0, abs=1e-6)
    assert analytics["value"]["start"] == 100000

    optimised = user_client.post(f"/api/v1/portfolios/{pid}/optimize", json={"max_weight": 0.5}).json()
    weights = optimised["comparison"]["optimised"]["weights"]
    assert sum(weights) == pytest.approx(1.0) and max(weights) <= 0.5 + 1e-6
    assert optimised["frontier"] and "not guaranteed" in optimised["disclaimer"]

    infeasible = user_client.post(f"/api/v1/portfolios/{pid}/optimize", json={"max_weight": 0.2})
    assert infeasible.status_code == 422 and infeasible.json()["error"]["code"] == "infeasible"

    applied = user_client.put(f"/api/v1/portfolios/{pid}/weights", json={"assets": [
        {"fund_id": fund_ids["FS-LC-001"], "weight_pct": 50}, {"fund_id": fund_ids["FS-DB-007"], "weight_pct": 50}]})
    assert applied.status_code == 200 and len(applied.json()["assets"]) == 2

    export = user_client.get(f"/api/v1/portfolios/{pid}/export.csv")
    assert export.status_code == 200 and export.text.startswith("scheme_code,fund_name")
    assert user_client.delete(f"/api/v1/portfolios/{pid}").status_code == 204
    assert user_client.get(f"/api/v1/portfolios/{pid}").status_code == 404


def test_portfolio_validation(user_client, fund_ids):
    bad_total = _create_portfolio(user_client, fund_ids, name="Bad", weights={"FS-LC-001": 60, "FS-MC-002": 30})
    assert bad_total.status_code == 422
    unknown = user_client.post("/api/v1/portfolios", json={"name": "U", "initial_value": 1,
                                                           "assets": [{"fund_id": 999999, "weight_pct": 100}]})
    assert unknown.status_code == 404


def test_portfolio_csv_import(user_client):
    content = b"scheme_code,weight_pct\nFS-LC-001,60\nFS-DB-007,40\n"
    created = user_client.post("/api/v1/portfolios/import", data={"name": "Imported", "initial_value": "50000"},
                               files={"file": ("p.csv", io.BytesIO(content), "text/csv")})
    assert created.status_code == 201 and len(created.json()["assets"]) == 2
    bad = user_client.post("/api/v1/portfolios/import", data={"name": "Bad import"},
                           files={"file": ("p.csv", io.BytesIO(b"scheme_code,weight_pct\nNOPE,100\n"), "text/csv")})
    assert bad.status_code == 422 and "unknown scheme_code" in str(bad.json())


def test_adhoc_optimisation_and_dashboard(user_client, fund_ids):
    result = user_client.post("/api/v1/portfolios/optimize", json={
        "fund_ids": [fund_ids["FS-LC-001"], fund_ids["FS-DB-007"]], "current_weights_pct": [50, 50],
        "objective": "min_variance"}).json()
    assert result["comparison"]["optimised"]["volatility"] <= result["comparison"]["current"]["volatility"]
    default = user_client.get("/api/v1/dashboard").json()
    assert default["selection"]["kind"] == "fund"
    pid = _create_portfolio(user_client, fund_ids, name="Dash").json()["id"]
    dash = user_client.get("/api/v1/dashboard", params={"portfolio_id": pid}).json()
    assert dash["selection"]["kind"] == "portfolio" and dash["selection"]["report"]["portfolio"]["contains_synthetic_data"]


def test_simulation_endpoints(user_client, fund_ids):
    sip = user_client.post("/api/v1/simulate/sip", json={"monthly_amount": 1000, "years": 1, "annual_return": 0}).json()
    assert sip["final_value"] == pytest.approx(12000)
    mc = user_client.post("/api/v1/simulate/monte-carlo", json={"monthly_amount": 1000, "years": 2, "annual_return": 0.1,
                                                                 "annual_volatility": 0.15, "paths": 200}).json()
    assert mc["kind"] == "monte_carlo" and len(mc["yearly"]) == 2
    ids = [fund_ids["FS-LC-001"], fund_ids["FS-DB-007"]]
    shock = user_client.post("/api/v1/simulate/market-shock", json={"fund_ids": ids, "weights_pct": [60, 40],
                                                                     "market_move": -0.2}).json()
    assert shock["estimated_portfolio_return"] < 0
    stress = user_client.post("/api/v1/simulate/stress", json={"fund_ids": ids, "weights_pct": [60, 40],
                                                                "volatility_multiplier": 2}).json()
    assert stress["scenario"]["volatility"] == pytest.approx(2 * stress["baseline"]["volatility"])
    change = user_client.post("/api/v1/simulate/allocation-change", json={
        "fund_ids": ids, "current_weights_pct": [50, 50], "proposed_weights_pct": [100, 0]}).json()
    assert change["proposed"]["volatility"] > change["current"]["volatility"]
    assert user_client.post("/api/v1/simulate/sip", json={"monthly_amount": -1, "years": 1, "annual_return": 0}).status_code == 422


def test_ml_forecast_anomalies_and_registry(user_client, fund_ids):
    fid = fund_ids["FS-MC-002"]
    body = {"fund_id": fid, "horizon_days": 21, "model_type": "ridge"}
    first = user_client.post("/api/v1/ml/forecast", json=body).json()
    assert {"test", "baseline_historical_mean", "beats_baseline"} <= set(first["evaluation"])
    assert first["explanation"]["available"] and first["forecast"]["interval_80"]
    second = user_client.post("/api/v1/ml/forecast", json=body).json()
    assert second["model"]["id"] == first["model"]["id"]  # unchanged data -> cached model reused
    models = user_client.get("/api/v1/ml/models", params={"fund_id": fid}).json()["items"]
    assert any(m["id"] == first["model"]["id"] for m in models)
    anomalies = user_client.post("/api/v1/ml/anomalies", json={"fund_id": fid, "contamination": 0.01}).json()
    assert "2025-08-21" in {a["date"] for a in anomalies["anomalies"]}  # planted fund-specific drop
    caps = user_client.get("/api/v1/ml/capabilities").json()
    assert next(m for m in caps["models"] if m["type"] == "lstm")["available"] is False
    assert user_client.post("/api/v1/ml/forecast", json={**body, "horizon_days": 7}).status_code == 422
