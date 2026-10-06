"""Whitelisted analytical operations the assistant may run.

Each tool has a Pydantic argument model. Whether the plan came from the
rule-based router or from a language model, arguments are validated here,
fund references are resolved against the database, and portfolios are
looked up *only* among the current user's portfolios. The model can never
run SQL, code or anything outside this table.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError, NotFoundError, ValidationFailedError
from app.models import Fund, Portfolio, User
from app.services.analytics import metrics, reports, series
from app.services.fund_mentions import resolve_fund_mentions
from app.services.ml import service as ml_service
from app.services.portfolio import analysis, optimizer

Period = Literal["1y", "3y", "5y", "max"]


@dataclass
class ToolContext:
    db: Session
    settings: Settings
    user: User


@dataclass
class ToolResult:
    title: str
    lines: list[str]
    data: dict = field(default_factory=dict)


def pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def resolve_fund(db: Session, reference: str) -> Fund:
    reference = reference.strip()
    fund = db.scalar(select(Fund).where(func.lower(Fund.scheme_code) == reference.lower()))
    if fund is not None:
        return fund
    matches = resolve_fund_mentions(db, reference)
    growth = [f for f in matches if f.return_basis == "nav_growth"] or matches
    if len(growth) == 1:
        return growth[0]
    if not growth:
        raise NotFoundError(f"No fund matches '{reference}'.")
    raise ValidationFailedError(f"'{reference}' matches several funds; use the scheme code.",
                                {"candidates": [f.scheme_code for f in growth]})


def _data_line(fund: Fund, freshness: dict) -> str:
    kind = "SYNTHETIC demo data" if fund.is_synthetic else f"source: {fund.source.name}"
    stale = " (stale)" if freshness.get("is_stale") else ""
    return f"Data: {kind}; latest NAV {freshness.get('latest_observation')}{stale}"


# --------------------------------------------------------------------------- tools


class FundMetricsArgs(BaseModel):
    fund: str = Field(min_length=1, max_length=120)
    period: Period = "3y"


def fund_metrics(ctx: ToolContext, args: FundMetricsArgs) -> ToolResult:
    fund = resolve_fund(ctx.db, args.fund)
    rf = (ctx.user.preferences or {}).get("risk_free_rate")
    report = reports.fund_risk_report(ctx.db, ctx.settings, fund, period=args.period, risk_free_rate=rf,
                                      include_series=False)
    m = report["metrics"]
    a = report["assumptions"]
    mdd = m["max_drawdown"]
    lines = [
        f"Period: {report['period']['start']} to {report['period']['end']} "
        f"({m['observations']['value']} daily returns, NAV-based)",
        f"Cumulative return: {pct(m['cumulative_return']['value'])}",
        f"CAGR: {pct(m['cagr']['value'])} per year",
        f"Annualised volatility: {pct(m['volatility']['value'])}",
        f"Sharpe ratio (risk-free {pct(a['risk_free_rate_annual'])}): {num(m['sharpe_ratio']['value'])}",
        f"Sortino ratio: {num(m['sortino_ratio']['value'])}",
        f"Maximum drawdown: {pct(mdd['value'])} (peak {mdd.get('peak_date')}, trough {mdd.get('trough_date')})",
        f"1-day historical VaR 95%: {pct(m['historical_var']['value'])}; "
        f"CVaR 95%: {pct(m['historical_cvar']['value'])}",
    ]
    if report["benchmark"] and "beta" in m:
        lines.append(f"Beta vs {report['benchmark']['name']}: {num(m['beta']['value'])}")
    lines.append(_data_line(fund, report["freshness"]))
    return ToolResult(f"Risk and return metrics - {fund.name}", lines,
                      {"fund_id": fund.id, "period": report["period"], "metrics": m})


class RiskChangeArgs(BaseModel):
    fund: str = Field(min_length=1, max_length=120)
    window_months: int = Field(default=6, ge=3, le=24)


def risk_change(ctx: ToolContext, args: RiskChangeArgs) -> ToolResult:
    """Compares risk in the most recent window with the window before it."""
    fund = resolve_fund(ctx.db, args.fund)
    nav = series.load_fund_nav(ctx.db, fund.id)
    end = nav.index[-1]
    mid = end - pd.DateOffset(months=args.window_months)
    start = mid - pd.DateOffset(months=args.window_months)
    recent, previous = nav.loc[mid:], nav.loc[start:mid]
    ppy = ctx.settings.trading_days_per_year
    rows = {}
    for label, window in (("previous", previous), ("recent", recent)):
        r = metrics.simple_returns(window)
        rows[label] = {
            "start": window.index[0].date().isoformat(), "end": window.index[-1].date().isoformat(),
            "volatility": metrics.annualized_volatility(r, ppy),
            "max_drawdown": metrics.max_drawdown(window).depth,
            "var95": metrics.historical_var(r, 0.95),
        }
    p, c = rows["previous"], rows["recent"]
    direction = "higher" if c["volatility"] > p["volatility"] else "lower"
    lines = [
        f"Previous window {p['start']} to {p['end']}: volatility {pct(p['volatility'])}, "
        f"max drawdown {pct(p['max_drawdown'])}, 1-day VaR 95% {pct(p['var95'])}",
        f"Recent window {c['start']} to {c['end']}: volatility {pct(c['volatility'])}, "
        f"max drawdown {pct(c['max_drawdown'])}, 1-day VaR 95% {pct(c['var95'])}",
        f"Annualised volatility is {direction} in the recent window "
        f"({pct(c['volatility'] - p['volatility'])} change in percentage points).",
        _data_line(fund, series.freshness(nav.index[-1].date(), ctx.settings.stale_after_days).as_dict()),
    ]
    return ToolResult(f"Risk change over the last {args.window_months} months - {fund.name}", lines,
                      {"fund_id": fund.id, "windows": rows})


class CompareArgs(BaseModel):
    funds: list[str] = Field(min_length=2, max_length=5)
    period: Period = "3y"


def compare_funds(ctx: ToolContext, args: CompareArgs) -> ToolResult:
    lines, data = [], []
    for reference in args.funds:
        fund = resolve_fund(ctx.db, reference)
        report = reports.fund_risk_report(ctx.db, ctx.settings, fund, period=args.period, include_series=False)
        m = report["metrics"]
        lines.append(
            f"{fund.name} ({report['period']['start']} to {report['period']['end']}): "
            f"CAGR {pct(m['cagr']['value'])}, volatility {pct(m['volatility']['value'])}, "
            f"Sharpe {num(m['sharpe_ratio']['value'])}, max drawdown {pct(m['max_drawdown']['value'])}"
        )
        data.append({"fund_id": fund.id, "metrics": {k: m[k]["value"] for k in
                                                       ("cagr", "volatility", "sharpe_ratio", "max_drawdown")}})
    lines.append("Comparison uses each fund's own history within the period; differing start dates are shown.")
    return ToolResult(f"Fund comparison ({args.period})", lines, {"funds": data})


class ForecastArgs(BaseModel):
    fund: str = Field(min_length=1, max_length=120)
    horizon_days: Literal[5, 21, 63] = 21
    model: Literal["ridge", "random_forest", "historical_mean"] = "random_forest"


def forecast(ctx: ToolContext, args: ForecastArgs) -> ToolResult:
    fund = resolve_fund(ctx.db, args.fund)
    result = ml_service.forecast(ctx.db, ctx.settings, fund, model_type=args.model, horizon=args.horizon_days,
                                 user_id=ctx.user.id, explain=False)
    f, ev = result["forecast"], result["evaluation"]
    lines = [
        f"Model: {args.model}, horizon {args.horizon_days} trading days from {f['as_of']}",
        f"Point estimate of return: {pct(f['predicted_return'])}; 80% interval "
        f"{pct(f['interval_80']['lower_return'])} to {pct(f['interval_80']['upper_return'])}",
        f"Held-out test MAE {num(ev['test']['mae'], 4)} vs historical-mean baseline "
        f"{num(ev['baseline_historical_mean']['mae'], 4)} (log-return units)",
        "The model beat the baseline on the test period." if ev["beats_baseline"] else
        "The model did NOT beat the simple baseline on the test period; treat the estimate as unreliable.",
        "This is a statistical estimate, not a guaranteed outcome.",
    ]
    return ToolResult(f"Forecast - {fund.name}", lines, {"fund_id": fund.id, "forecast": f, "evaluation": ev})


class PortfolioArgs(BaseModel):
    portfolio: str = Field(min_length=1, max_length=100)
    period: Period = "3y"


def _find_portfolio(ctx: ToolContext, reference: str) -> Portfolio:
    query = select(Portfolio).where(Portfolio.user_id == ctx.user.id)  # ownership is always enforced
    portfolios = ctx.db.scalars(query).all()
    for p in portfolios:
        if str(p.id) == reference or p.name.lower() == reference.lower():
            return p
    if reference.lower() in ("my portfolio", "portfolio", "default") and len(portfolios) == 1:
        return portfolios[0]
    raise NotFoundError("No portfolio of yours matches that name.",
                        {"your_portfolios": [p.name for p in portfolios]})


def portfolio_summary(ctx: ToolContext, args: PortfolioArgs) -> ToolResult:
    portfolio = _find_portfolio(ctx, args.portfolio)
    report = analysis.portfolio_report(ctx.db, ctx.settings, portfolio, period=args.period)
    m = report["metrics"]
    lines = [
        f"Value: INR {report['value']['start']:,.0f} at {report['period']['start']} -> "
        f"INR {report['value']['end']:,.0f} at {report['period']['end']} (buy and hold)",
        f"CAGR {pct(m['cagr']['value'])}, volatility {pct(m['volatility']['value'])}, "
        f"Sharpe {num(m['sharpe_ratio']['value'])}, max drawdown {pct(m['max_drawdown']['value'])}",
        "Current weights: " + ", ".join(f"{h['scheme_code']} {pct(h['current_weight'], 1)}"
                                        for h in report["allocation"]["holdings"]),
        f"Effective number of holdings: {num(report['allocation']['effective_number_of_holdings'])}",
    ]
    return ToolResult(f"Portfolio - {portfolio.name}", lines, {"portfolio_id": str(portfolio.id)})


class OptimiseArgs(BaseModel):
    portfolio: str = Field(min_length=1, max_length=100)
    risk_profile: Literal["conservative", "moderate", "aggressive"] = "moderate"
    max_weight: float = Field(default=0.6, gt=0, le=1)


def optimise_portfolio(ctx: ToolContext, args: OptimiseArgs) -> ToolResult:
    portfolio = _find_portfolio(ctx, args.portfolio)
    assets = sorted(portfolio.assets, key=lambda a: a.fund_id)
    result = analysis.optimise(
        ctx.db, ctx.settings, fund_ids=[a.fund_id for a in assets],
        current_weights=[float(a.weight) for a in assets], objective="mean_variance",
        risk_aversion=optimizer.RISK_PROFILES[args.risk_profile], min_weight=0.0, max_weight=args.max_weight,
        frontier_points=0,
    )
    opt, cur = result["comparison"]["optimised"], result["comparison"]["current"]
    lines = [
        f"Objective: mean-variance utility, risk profile {args.risk_profile} "
        f"(risk aversion {optimizer.RISK_PROFILES[args.risk_profile]}), max weight {pct(args.max_weight, 0)}",
        "Optimised weights: " + ", ".join(f"{a['scheme_code']} {pct(w, 1)}"
                                          for a, w in zip(result["assets"], opt["weights"], strict=True)),
        f"Historical expected return {pct(opt['expected_return'])} vs current {pct(cur['expected_return'])}; "
        f"volatility {pct(opt['volatility'])} vs {pct(cur['volatility'])}",
        f"Estimation window {result['estimation']['start']} to {result['estimation']['end']}. "
        "Optimal for past data only; not a guarantee of future performance.",
    ]
    return ToolResult(f"Optimisation - {portfolio.name}", lines, {"result": {k: result[k] for k in
                                                                             ("assets", "comparison", "estimation")}})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[ToolContext, BaseModel], ToolResult]


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec for spec in (
        ToolSpec("fund_metrics", "Historical return and risk metrics for one fund.", FundMetricsArgs, fund_metrics),
        ToolSpec("risk_change", "Compare a fund's recent risk with the preceding period.", RiskChangeArgs, risk_change),
        ToolSpec("compare_funds", "Compare 2-5 funds' historical metrics.", CompareArgs, compare_funds),
        ToolSpec("forecast", "Evaluated statistical forecast of a fund's return.", ForecastArgs, forecast),
        ToolSpec("portfolio_summary", "Analytics for one of the user's portfolios.", PortfolioArgs, portfolio_summary),
        ToolSpec("optimise_portfolio", "Mean-variance optimisation of one of the user's portfolios.",
                 OptimiseArgs, optimise_portfolio),
    )
}


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ExecutedTool:
    call: ToolCall
    result: ToolResult | None
    error: str | None


def execute(ctx: ToolContext, call: ToolCall) -> ExecutedTool:
    spec = TOOLS.get(call.name)
    if spec is None:
        return ExecutedTool(call, None, f"Unknown tool '{call.name}'.")
    try:
        args = spec.args_model.model_validate(call.args)
    except ValidationError as exc:
        return ExecutedTool(call, None, f"Invalid arguments for {call.name}: {exc.errors()[0]['msg']}")
    try:
        return ExecutedTool(call, spec.run(ctx, args), None)
    except AppError as exc:
        ctx.db.rollback()
        return ExecutedTool(call, None, exc.message)


def tool_catalog() -> list[dict]:
    return [{"name": s.name, "description": s.description, "arguments": s.args_model.model_json_schema()}
            for s in TOOLS.values()]


