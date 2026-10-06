"""Mutual fund explorer endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.core.errors import InsufficientDataError, NotFoundError, ValidationFailedError
from app.models import Benchmark, Fund, FundAum, FundHolding, FundSipFlow, NavObservation
from app.services.analytics import metrics, reports, series

router = APIRouter(tags=["funds"])

PeriodParam = Annotated[Literal["1m", "3m", "6m", "1y", "3y", "5y", "max"], Query()]


def _fund_summary(db, settings, fund: Fund) -> dict:  # type: ignore[no-untyped-def]
    latest_row = db.execute(
        select(NavObservation.obs_date, NavObservation.nav).where(NavObservation.fund_id == fund.id)
        .order_by(NavObservation.obs_date.desc()).limit(1)
    ).first()
    aum = db.execute(select(FundAum.as_of_date, FundAum.aum_crore).where(FundAum.fund_id == fund.id)
                     .order_by(FundAum.as_of_date.desc()).limit(1)).first()
    one_year = None
    if latest_row:
        start = (pd.Timestamp(latest_row[0]) - pd.DateOffset(years=1)).date()
        base = db.execute(select(NavObservation.obs_date, NavObservation.nav)
                          .where(NavObservation.fund_id == fund.id, NavObservation.obs_date <= start)
                          .order_by(NavObservation.obs_date.desc()).limit(1)).first()
        if base and (start - base[0]).days <= 7:
            one_year = float(latest_row[1] / base[1] - 1)
    return {
        "id": fund.id, "scheme_code": fund.scheme_code, "name": fund.name, "amc": fund.amc,
        "category": fund.category, "asset_class": fund.asset_class, "plan": fund.plan, "option": fund.option,
        "risk_label": fund.risk_label, "is_synthetic": fund.is_synthetic, "source": fund.source.name,
        "return_basis": fund.return_basis,
        "expense_ratio_pct": float(fund.expense_ratio_pct) if fund.expense_ratio_pct is not None else None,
        "benchmark": {"id": fund.benchmark.id, "name": fund.benchmark.name} if fund.benchmark else None,
        "latest_nav": float(latest_row[1]) if latest_row else None,
        "latest_nav_date": latest_row[0].isoformat() if latest_row else None,
        "freshness": series.freshness(latest_row[0] if latest_row else None, settings.stale_after_days).as_dict(),
        "return_1y": one_year,
        "aum": {"value_crore": float(aum[1]), "as_of": aum[0].isoformat()} if aum else None,
    }


@router.get("/funds")
def list_funds(
    db: DbSession, settings: AppSettings, _: CurrentUser,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    asset_class: Annotated[str | None, Query(max_length=24)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    query = select(Fund)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(Fund.name).like(pattern), func.lower(Fund.scheme_code).like(pattern)))
    if category:
        query = query.where(Fund.category == category)
    if asset_class:
        query = query.where(Fund.asset_class == asset_class)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    funds = db.scalars(query.order_by(Fund.name).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_fund_summary(db, settings, f) for f in funds], "total": total, "page": page,
            "page_size": page_size}


@router.get("/funds/categories")
def categories(db: DbSession, _: CurrentUser) -> dict:
    rows = db.execute(select(Fund.category, Fund.asset_class, func.count()).group_by(Fund.category, Fund.asset_class)
                      .order_by(Fund.category)).all()
    return {"items": [{"category": c, "asset_class": a, "count": n} for c, a, n in rows]}


@router.get("/funds/compare")
def compare(
    db: DbSession, settings: AppSettings, _: CurrentUser,
    ids: Annotated[str, Query(description="Comma-separated fund ids (2-5)", max_length=100)],
    period: PeriodParam = "3y",
) -> dict:
    try:
        fund_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError as exc:
        raise ValidationFailedError("ids must be comma-separated integers.") from exc
    if not 2 <= len(set(fund_ids)) <= 5:
        raise ValidationFailedError("Compare between 2 and 5 distinct funds.")
    funds = [series.get_fund(db, fid) for fid in dict.fromkeys(fund_ids)]
    rows = []
    for fund in funds:
        report = reports.fund_risk_report(db, settings, fund, period=period, include_series=False)
        rows.append({"fund": _fund_summary(db, settings, fund), "period": report["period"],
                     "metrics": report["metrics"]})
    frame, alignment = series.load_nav_frame(db, [f.id for f in funds])
    latest = frame.index[-1].date()
    start, end = series.resolve_period(period, latest, frame.index[0].date())
    window = frame.loc[pd.Timestamp(start):pd.Timestamp(end)]
    rebased = window / window.iloc[0] * 100
    chart = [{"date": d.date().isoformat(), **{str(fid): round(float(rebased.loc[d, fid]), 3) for fid in rebased}}
             for d in rebased.index]
    return {"funds": rows, "chart": {"common_start": window.index[0].date().isoformat(), "points": chart,
                                     "alignment": alignment,
                                     "history_covers_period": series.history_covers(period, latest,
                                                                                    frame.index[0].date())}}


@router.get("/funds/{fund_id}")
def fund_detail(fund_id: int, db: DbSession, settings: AppSettings, _: CurrentUser) -> dict:
    fund = series.get_fund(db, fund_id)
    out = _fund_summary(db, settings, fund)
    out["launch_date"] = fund.launch_date.isoformat() if fund.launch_date else None
    try:
        nav = series.load_fund_nav(db, fund.id)
        out["trailing_returns"] = reports.trailing_returns(nav)
        out["history"] = {"start": nav.index[0].date().isoformat(), "end": nav.index[-1].date().isoformat(),
                          "observations": int(len(nav))}
    except InsufficientDataError:
        out["trailing_returns"] = []
        out["history"] = None
    holding_dates = db.scalars(select(FundHolding.as_of_date).where(FundHolding.fund_id == fund.id)
                               .distinct().order_by(FundHolding.as_of_date.desc())).all()
    out["holdings_dates"] = [d.isoformat() for d in holding_dates]
    out["has_sip_data"] = db.scalar(select(func.count()).where(FundSipFlow.fund_id == fund.id)) > 0
    out["data_notes"] = []
    if not holding_dates:
        out["data_notes"].append("No holdings disclosure is available for this fund.")
    if not out["has_sip_data"]:
        out["data_notes"].append("SIP flow data is not available for this fund.")
    if fund.return_basis == "nav_price":
        out["data_notes"].append("Income-distribution (IDCW) option: NAV returns exclude payouts.")
    return out


@router.get("/funds/{fund_id}/nav")
def fund_nav(fund_id: int, db: DbSession, _: CurrentUser, start: date | None = None,
             end: date | None = None) -> dict:
    series.get_fund(db, fund_id)
    nav = series.load_fund_nav(db, fund_id, start, end)
    return {"fund_id": fund_id, "unit": "INR per unit",
            "points": [{"date": d.date().isoformat(), "nav": round(float(v), 4)} for d, v in nav.items()]}


@router.get("/funds/{fund_id}/risk")
def fund_risk(
    fund_id: int, db: DbSession, settings: AppSettings, user: CurrentUser, period: PeriodParam = "3y",
    risk_free_rate: Annotated[float | None, Query(ge=0, le=0.2)] = None,
    confidence: Annotated[float, Query(ge=0.9, le=0.995)] = 0.95,
    start: date | None = None, end: date | None = None,
) -> dict:
    fund = series.get_fund(db, fund_id)
    rf = risk_free_rate if risk_free_rate is not None else (user.preferences or {}).get("risk_free_rate")
    return reports.fund_risk_report(db, settings, fund, period=period, start=start, end=end,
                                    risk_free_rate=rf, confidence=confidence)


@router.get("/funds/{fund_id}/holdings")
def fund_holdings(fund_id: int, db: DbSession, _: CurrentUser, as_of: date | None = None) -> dict:
    fund = series.get_fund(db, fund_id)
    if as_of is None:
        as_of = db.scalar(select(func.max(FundHolding.as_of_date)).where(FundHolding.fund_id == fund.id))
    if as_of is None:
        return {"fund_id": fund.id, "as_of": None, "holdings": [], "sectors": [],
                "note": "No holdings disclosure is available for this fund."}
    rows = db.scalars(select(FundHolding).where(FundHolding.fund_id == fund.id, FundHolding.as_of_date == as_of)
                      .order_by(FundHolding.weight_pct.desc())).all()
    if not rows:
        raise NotFoundError("No holdings for that date.")
    sectors: dict[str, float] = {}
    for h in rows:
        sectors[h.sector] = sectors.get(h.sector, 0.0) + float(h.weight_pct)
    securities = [h for h in rows if h.asset_type != "cash"]
    weights = [float(h.weight_pct) for h in securities]
    return {
        "fund_id": fund.id, "as_of": as_of.isoformat(), "is_synthetic": fund.is_synthetic,
        "holdings": [{"name": h.holding_name, "sector": h.sector, "asset_type": h.asset_type,
                      "weight_pct": float(h.weight_pct)} for h in rows],
        "sectors": [{"sector": k, "weight_pct": round(v, 3)}
                    for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])],
        "concentration": {
            "top10_weight_pct": round(sum(sorted(weights, reverse=True)[:10]), 3),
            "herfindahl_index": metrics.herfindahl_index(weights) if weights else None,
            "effective_number_of_holdings": metrics.effective_number_of_holdings(weights) if weights else None,
            "note": "Concentration excludes cash; HHI computed on security weights re-normalised to 100%.",
        },
        "total_weight_pct": round(sum(float(h.weight_pct) for h in rows), 3),
    }


@router.get("/funds/{fund_id}/aum")
def fund_aum(fund_id: int, db: DbSession, _: CurrentUser) -> dict:
    fund = series.get_fund(db, fund_id)
    rows = db.execute(select(FundAum.as_of_date, FundAum.aum_crore).where(FundAum.fund_id == fund.id)
                      .order_by(FundAum.as_of_date)).all()
    return {"fund_id": fund.id, "unit": "INR crore", "is_synthetic": fund.is_synthetic,
            "points": [{"date": d.isoformat(), "value": float(v)} for d, v in rows]}


@router.get("/funds/{fund_id}/sip")
def fund_sip(fund_id: int, db: DbSession, _: CurrentUser) -> dict:
    fund = series.get_fund(db, fund_id)
    rows = db.execute(select(FundSipFlow.month, FundSipFlow.sip_inflow_crore, FundSipFlow.sip_accounts)
                      .where(FundSipFlow.fund_id == fund.id).order_by(FundSipFlow.month)).all()
    return {"fund_id": fund.id, "unit": "INR crore per month", "is_synthetic": fund.is_synthetic,
            "points": [{"month": m.isoformat(), "inflow": float(v), "accounts": a} for m, v, a in rows],
            "note": None if rows else "SIP flow data is not available for this fund."}


@router.get("/benchmarks")
def list_benchmarks(db: DbSession, _: CurrentUser) -> dict:
    rows = db.scalars(select(Benchmark).order_by(Benchmark.name)).all()
    return {"items": [{"id": b.id, "code": b.code, "name": b.name, "return_basis": b.return_basis,
                       "is_synthetic": b.is_synthetic} for b in rows]}


@router.get("/benchmarks/{benchmark_id}/series")
def benchmark_series(benchmark_id: int, db: DbSession, _: CurrentUser, start: date | None = None,
                     end: date | None = None) -> dict:
    bench = series.get_benchmark(db, benchmark_id)
    values = series.load_benchmark(db, bench.id, start, end)
    return {"benchmark_id": bench.id, "name": bench.name,
            "points": [{"date": d.date().isoformat(), "value": round(float(v), 3)} for d, v in values.items()]}


class CorrelationRequest(BaseModel):
    fund_ids: list[int] = Field(min_length=2, max_length=12)
    start: date | None = None
    end: date | None = None


@router.post("/analytics/correlation")
def correlation(body: CorrelationRequest, db: DbSession, _: CurrentUser) -> dict:
    ids = list(dict.fromkeys(body.fund_ids))
    funds = [series.get_fund(db, fid) for fid in ids]
    result = reports.correlation_report(db, ids, body.start, body.end)
    result["labels"] = [f.scheme_code for f in funds]
    result["names"] = [f.name for f in funds]
    return result
