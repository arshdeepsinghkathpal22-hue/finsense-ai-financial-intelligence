"""Portfolio analytics and optimisation built on stored NAV history."""

from __future__ import annotations

from datetime import date
from typing import Literal

import numpy as np
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import InfeasibleError, InsufficientDataError, ValidationFailedError
from app.models import Fund, Portfolio
from app.services.analytics import metrics, reports, series
from app.services.portfolio import optimizer

Objective = Literal["mean_variance", "min_variance", "max_sharpe"]


def _funds_and_weights(portfolio: Portfolio) -> tuple[list[Fund], np.ndarray]:
    if not portfolio.assets:
        raise ValidationFailedError("The portfolio has no assets.")
    assets = sorted(portfolio.assets, key=lambda a: a.fund_id)
    funds = [a.fund for a in assets]
    weights = np.array([float(a.weight) for a in assets])
    return funds, metrics.normalise_weights(weights)


def portfolio_report(
    db: Session, settings: Settings, portfolio: Portfolio, *, period: str = "3y",
    risk_free_rate: float | None = None, confidence: float = 0.95,
) -> dict:
    funds, weights = _funds_and_weights(portfolio)
    conv = reports.conventions_for(settings, risk_free_rate, confidence)
    full, _ = series.load_nav_frame(db, [f.id for f in funds])
    latest = full.index[-1].date()
    if portfolio.start_date:
        start, end = portfolio.start_date, latest
        period_label = "since_start_date"
    else:
        start, end = series.resolve_period(period, latest, full.index[0].date())
        period_label = period
    prices, alignment = series.load_nav_frame(db, [f.id for f in funds], start, end)
    initial_value = float(portfolio.initial_value)
    value = metrics.buy_and_hold_value(prices, weights, initial_value)

    bench_prices = None
    benchmark = None
    benchmark_id = portfolio.benchmark_id or funds[int(np.argmax(weights))].benchmark_id
    if benchmark_id:
        benchmark = series.get_benchmark(db, benchmark_id)
        try:
            bench_prices = series.load_benchmark(db, benchmark_id, prices.index[0].date(), end)
        except InsufficientDataError:
            bench_prices = None

    returns = prices.pct_change().dropna()
    cov = returns.cov().to_numpy() * conv.periods_per_year
    drifted = metrics.current_weights(prices, weights)
    try:
        div_ratio = metrics.diversification_ratio(weights, cov)
    except ValueError:
        div_ratio = None
    port_vol = metrics.portfolio_volatility(weights, cov)
    # Risk contribution of each asset: w_i * (Sigma w)_i / sigma_p.
    marginal = cov @ weights
    risk_contrib = (weights * marginal / port_vol) if port_vol > 0 else np.zeros_like(weights)

    holdings = []
    for i, fund in enumerate(funds):
        holdings.append({
            "fund_id": fund.id, "scheme_code": fund.scheme_code, "name": fund.name,
            "category": fund.category, "asset_class": fund.asset_class,
            "is_synthetic": fund.is_synthetic,
            "initial_weight": float(weights[i]), "current_weight": float(drifted[i]),
            "current_value": float(value.iloc[-1] * drifted[i]),
            "risk_contribution": float(risk_contrib[i] / port_vol) if port_vol > 0 else None,
        })

    by_asset_class: dict[str, float] = {}
    for h in holdings:
        by_asset_class[h["asset_class"]] = by_asset_class.get(h["asset_class"], 0.0) + h["current_weight"]

    corr = returns.corr()
    report = {
        "portfolio": {"id": str(portfolio.id), "name": portfolio.name,
                      "initial_value": initial_value,
                      "contains_synthetic_data": any(f.is_synthetic for f in funds)},
        "period": {"label": period_label, "start": prices.index[0].date().isoformat(),
                   "end": prices.index[-1].date().isoformat()},
        "freshness": series.freshness(prices.index[-1].date(), settings.stale_after_days).as_dict(),
        "benchmark": {"id": benchmark.id, "name": benchmark.name, "return_basis": benchmark.return_basis}
        if benchmark else None,
        "assumptions": reports.assumptions(conv, "portfolio_buy_and_hold")
        | {"rebalancing": "None (buy and hold from the start date; weights drift)."},
        "alignment": alignment,
        "value": {"start": initial_value, "end": float(value.iloc[-1]),
                  "currency": "INR", "as_of": prices.index[-1].date().isoformat()},
        "metrics": reports.risk_metrics_block(value, bench_prices, conv),
        "allocation": {
            "holdings": holdings,
            "by_asset_class": [{"asset_class": k, "weight": v} for k, v in sorted(by_asset_class.items())],
            "herfindahl_index": metrics.herfindahl_index(drifted),
            "effective_number_of_holdings": metrics.effective_number_of_holdings(drifted),
            "diversification_ratio": div_ratio,
            "largest_weight": float(drifted.max()),
        },
        "correlation": {
            "labels": [f.scheme_code for f in funds],
            "matrix": [[round(float(corr.iloc[i, j]), 4) for j in range(len(funds))] for i in range(len(funds))],
        },
        "series": {
            "value": reports._series_points(value, 2),
            "drawdown": reports._series_points(metrics.drawdown_series(value)),
        },
    }
    if bench_prices is not None:
        b = bench_prices.loc[bench_prices.index >= prices.index[0]]
        report["series"]["benchmark_scaled"] = reports._series_points(b / b.iloc[0] * initial_value, 2)
    return report


def optimise(
    db: Session, settings: Settings, *, fund_ids: list[int], current_weights: list[float] | None,
    objective: Objective, risk_aversion: float, min_weight: float, max_weight: float,
    lookback: str = "3y", end: date | None = None, risk_free_rate: float | None = None,
    frontier_points: int = 25,
) -> dict:
    if len(set(fund_ids)) != len(fund_ids):
        raise ValidationFailedError("Each fund may appear only once.")
    if len(fund_ids) < 2:
        raise ValidationFailedError("Optimisation needs at least two assets.")
    funds = [series.get_fund(db, fid) for fid in fund_ids]
    rf = settings.risk_free_rate if risk_free_rate is None else risk_free_rate
    full, _ = series.load_nav_frame(db, fund_ids, None, end)
    start, window_end = series.resolve_period(lookback, full.index[-1].date(), full.index[0].date())
    prices, alignment = series.load_nav_frame(db, fund_ids, start, window_end)
    returns = prices.pct_change().dropna().to_numpy()
    est = optimizer.estimate_inputs(returns, settings.trading_days_per_year)

    if objective == "mean_variance":
        result = optimizer.optimise_mean_variance(
            est, risk_aversion=risk_aversion, min_weight=min_weight, max_weight=max_weight,
            risk_free_rate=rf)
    elif objective == "min_variance":
        result = optimizer.minimum_variance(est, min_weight=min_weight, max_weight=max_weight, risk_free_rate=rf)
    else:
        result = optimizer.maximum_sharpe(est, min_weight=min_weight, max_weight=max_weight, risk_free_rate=rf)

    def describe(w: np.ndarray) -> dict:
        ret = metrics.portfolio_expected_return(w, est.expected_returns)
        vol = metrics.portfolio_volatility(w, est.covariance)
        return {"weights": [float(x) for x in w], "expected_return": ret, "volatility": vol,
                "sharpe": (ret - rf) / vol if vol > 0 else None,
                "herfindahl_index": metrics.herfindahl_index(w)}

    equal = np.full(len(funds), 1.0 / len(funds))
    comparison = {"optimised": describe(result.weights), "equal_weight": describe(equal)}
    if current_weights is not None:
        if len(current_weights) != len(funds):
            raise ValidationFailedError("Provide one current weight per fund.")
        comparison["current"] = describe(metrics.normalise_weights(current_weights))

    frontier: list[dict] = []
    if frontier_points > 0:
        try:
            frontier = optimizer.efficient_frontier(
                est, min_weight=min_weight, max_weight=max_weight, points=frontier_points, risk_free_rate=rf)
        except (InfeasibleError, ValidationFailedError):
            frontier = []  # the frontier is a visual aid; the main result stands on its own

    return {
        "assets": [{"fund_id": f.id, "scheme_code": f.scheme_code, "name": f.name,
                    "is_synthetic": f.is_synthetic,
                    "expected_return": float(est.expected_returns[i]),
                    "volatility": float(np.sqrt(est.covariance[i, i]))} for i, f in enumerate(funds)],
        "objective": objective,
        "risk_aversion": risk_aversion if objective == "mean_variance" else None,
        "constraints": {"min_weight": min_weight, "max_weight": max_weight, "long_only": True,
                        "sum_to_one": True},
        "estimation": {
            "lookback": lookback, "start": prices.index[0].date().isoformat(),
            "end": prices.index[-1].date().isoformat(), "observations": int(len(returns)),
            "alignment": alignment, "shrinkage_applied": est.shrinkage_applied,
            "shrinkage_intensity": est.shrinkage_intensity, "notes": est.notes,
            "risk_free_rate": rf,
        },
        "solver": {"method": "SLSQP", "iterations": result.iterations, "converged": True},
        "comparison": comparison,
        "frontier": frontier,
        "disclaimer": (
            "Expected returns and covariances are historical estimates over the lookback window. "
            "An allocation that was optimal in the past is not guaranteed to perform well in the "
            "future; results are highly sensitive to the estimation period."
        ),
    }


def historical_inputs(db: Session, settings: Settings, fund_ids: list[int], lookback: str = "3y") -> dict:
    """Annualised mean returns, volatilities, correlations and betas for scenario tools."""
    funds = [series.get_fund(db, fid) for fid in fund_ids]
    full, _ = series.load_nav_frame(db, fund_ids)
    start, end = series.resolve_period(lookback, full.index[-1].date(), full.index[0].date())
    prices, alignment = series.load_nav_frame(db, fund_ids, start, end)
    returns = prices.pct_change().dropna()
    est = optimizer.estimate_inputs(returns.to_numpy(), settings.trading_days_per_year)
    vols = np.sqrt(np.diag(est.covariance))
    corr = est.covariance / np.outer(vols, vols)
    betas: list[float | None] = []
    for fund in funds:
        beta = None
        if fund.benchmark_id:
            try:
                bench = series.load_benchmark(db, fund.benchmark_id, prices.index[0].date(), end)
                stats_ = metrics.benchmark_statistics(
                    returns[fund.id], metrics.simple_returns(bench),
                    reports.conventions_for(settings))
                beta = stats_.beta
            except (InsufficientDataError, ValueError):
                beta = None
        betas.append(beta)
    return {
        "funds": funds, "labels": [f.scheme_code for f in funds],
        "expected_returns": est.expected_returns.tolist(), "volatilities": vols.tolist(),
        "correlation": corr.tolist(), "betas": betas,
        "window": {"start": prices.index[0].date().isoformat(), "end": prices.index[-1].date().isoformat(),
                   "observations": int(len(returns)), "alignment": alignment},
    }
