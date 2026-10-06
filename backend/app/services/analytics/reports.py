"""Assembles metric calculations into API-ready reports with their assumptions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import InsufficientDataError
from app.models import Fund
from app.services.analytics import metrics, series
from app.services.analytics.metrics import Conventions

ROLLING_WINDOW = 126  # ~6 months of trading days
TRAILING_PERIODS = ("1m", "3m", "6m", "1y", "3y", "5y")


def conventions_for(settings: Settings, risk_free_rate: float | None = None,
                    confidence: float = 0.95) -> Conventions:
    return Conventions(
        periods_per_year=settings.trading_days_per_year,
        risk_free_rate=settings.risk_free_rate if risk_free_rate is None else risk_free_rate,
        var_confidence=confidence,
    )


def _attempt(fn: Callable[[], Any]) -> tuple[Any, str | None]:
    """Runs a calculation, turning 'not enough data' into an explained gap."""
    try:
        return fn(), None
    except (InsufficientDataError, ValueError) as exc:
        return None, str(exc)


def _metric(value: Any, unit: str, reason: str | None = None, **extra: Any) -> dict:
    out = {"value": value, "unit": unit}
    if reason:
        out["unavailable_reason"] = reason
    out.update(extra)
    return out


def assumptions(conv: Conventions, return_basis: str) -> dict:
    basis_text = {
        "nav_growth": "NAV-based returns of a growth option (income reinvested, net of expenses).",
        "nav_price": "NAV-based price returns of an income-distribution option; payouts are NOT included.",
        "portfolio_buy_and_hold": "Buy-and-hold portfolio value from constituent growth-option NAVs.",
    }.get(return_basis, return_basis)
    return {
        "frequency": "daily (trading days)",
        "periods_per_year": conv.periods_per_year,
        "risk_free_rate_annual": conv.risk_free_rate,
        "return_basis": return_basis,
        "return_basis_note": basis_text,
        "var_method": (
            f"Historical one-day VaR/CVaR at {conv.var_confidence:.0%} confidence "
            "(empirical quantile, linear interpolation). Parametric figures assume normally "
            "distributed daily returns. Different estimators give different values."
        ),
        "sharpe_method": "Mean daily excess return / standard deviation, scaled by sqrt(periods per year).",
        "sortino_method": "Mean daily excess return / downside deviation below the risk-free rate.",
    }


def risk_metrics_block(prices: pd.Series, bench_prices: pd.Series | None, conv: Conventions) -> dict:
    returns = metrics.simple_returns(prices)
    out: dict[str, dict] = {}

    value, reason = _attempt(lambda: metrics.cumulative_return(prices))
    out["cumulative_return"] = _metric(value, "fraction", reason)
    value, reason = _attempt(lambda: metrics.cagr(prices))
    out["cagr"] = _metric(value, "fraction/yr", reason)
    value, reason = _attempt(lambda: metrics.annualized_mean_return(returns, conv.periods_per_year))
    out["annualized_mean_return"] = _metric(value, "fraction/yr", reason)
    value, reason = _attempt(lambda: metrics.annualized_volatility(returns, conv.periods_per_year))
    out["volatility"] = _metric(value, "fraction/yr", reason)

    value, reason = _attempt(lambda: metrics.sharpe_ratio(returns, conv))
    if value is None and reason is None:
        reason = "Returns have zero variance."
    out["sharpe_ratio"] = _metric(value, "ratio", reason)
    value, reason = _attempt(lambda: metrics.sortino_ratio(returns, conv))
    if value is None and reason is None:
        reason = "No returns fell below the risk-free target."
    out["sortino_ratio"] = _metric(value, "ratio", reason)

    mdd, reason = _attempt(lambda: metrics.max_drawdown(prices))
    out["max_drawdown"] = _metric(
        mdd.depth if mdd else None,
        "fraction",
        reason,
        peak_date=mdd.peak_date.isoformat() if mdd else None,
        trough_date=mdd.trough_date.isoformat() if mdd else None,
        recovery_date=mdd.recovery_date.isoformat() if mdd and mdd.recovery_date else None,
    )

    c = conv.var_confidence
    for key, fn in (
        ("historical_var", metrics.historical_var),
        ("historical_cvar", metrics.historical_cvar),
        ("parametric_var", metrics.parametric_var),
        ("parametric_cvar", metrics.parametric_cvar),
    ):
        value, reason = _attempt(lambda fn=fn: fn(returns, c))
        out[key] = _metric(value, "fraction of value, 1 day", reason, confidence=c)

    if bench_prices is not None:
        bench_returns = metrics.simple_returns(bench_prices)
        stats_, reason = _attempt(lambda: metrics.benchmark_statistics(returns, bench_returns, conv))
        for key in ("beta", "correlation", "alpha_annual", "tracking_error", "information_ratio"):
            unit = {"beta": "ratio", "correlation": "ratio", "information_ratio": "ratio"}.get(
                key, "fraction/yr"
            )
            val = getattr(stats_, key) if stats_ else None
            out[key] = _metric(val, unit, reason if stats_ is None else None)
        if stats_:
            out["beta"]["observations"] = stats_.observations
    out["observations"] = _metric(int(len(returns)), "daily returns")
    return out


def _series_points(values: pd.Series, digits: int = 6) -> list[dict]:
    return [
        {"date": pd.Timestamp(idx).date().isoformat(), "value": round(float(v), digits)}
        for idx, v in values.items()
        if np.isfinite(v)
    ]


def trailing_returns(prices: pd.Series) -> list[dict]:
    """Point-to-point returns over standard periods ending at the latest NAV.

    Periods longer than one year are annualised (CAGR), as is customary for
    mutual fund factsheets; shorter ones are absolute.
    """
    latest = prices.index[-1].date()
    earliest = prices.index[0].date()
    rows = []
    for label in TRAILING_PERIODS:
        start, _ = series.resolve_period(label, latest, earliest)
        window = prices.loc[pd.Timestamp(start):]
        covered = series.history_covers(label, latest, earliest)
        annualised = series.PERIOD_YEARS[label] > 1
        if not covered or len(window) < 2:
            rows.append({"period": label, "value": None, "annualised": annualised,
                         "unavailable_reason": "History does not cover this period."})
            continue
        value = metrics.cagr(window) if annualised else metrics.cumulative_return(window)
        rows.append({"period": label, "value": value, "annualised": annualised,
                     "start_date": window.index[0].date().isoformat()})
    rows.append({
        "period": "since_start",
        "value": metrics.cagr(prices) if (prices.index[-1] - prices.index[0]).days > 365 else
        metrics.cumulative_return(prices),
        "annualised": (prices.index[-1] - prices.index[0]).days > 365,
        "start_date": earliest.isoformat(),
    })
    return rows


def _period_block(period: str, start: date | None, end: date | None, prices: pd.Series,
                  latest: date, earliest: date) -> dict:
    block = {"label": period if not (start or end) else "custom",
             "start": prices.index[0].date().isoformat(),
             "end": prices.index[-1].date().isoformat(),
             "history_covers_period": True}
    if not (start or end) and not series.history_covers(period, latest, earliest):
        block["history_covers_period"] = False
        block["note"] = (f"Available history starts on {earliest.isoformat()}, which is shorter than the "
                         f"requested {period} period; metrics cover the available history only.")
    return block


def fund_risk_report(
    db: Session,
    settings: Settings,
    fund: Fund,
    *,
    period: str = "3y",
    start: date | None = None,
    end: date | None = None,
    risk_free_rate: float | None = None,
    confidence: float = 0.95,
    include_series: bool = True,
) -> dict:
    conv = conventions_for(settings, risk_free_rate, confidence)
    all_prices = series.load_fund_nav(db, fund.id)
    latest = all_prices.index[-1].date()
    window_start, window_end = series.resolve_period(
        period, latest, all_prices.index[0].date(), start, end
    )
    prices = all_prices.loc[pd.Timestamp(window_start): pd.Timestamp(window_end)]
    if len(prices) < 2:
        raise InsufficientDataError("Not enough NAV observations in the selected period.")

    bench_prices = None
    bench_info = None
    if fund.benchmark_id is not None:
        try:
            bench_prices = series.load_benchmark(db, fund.benchmark_id, window_start, window_end)
            bench_info = {
                "id": fund.benchmark.id,
                "name": fund.benchmark.name,
                "return_basis": fund.benchmark.return_basis,
                "is_synthetic": fund.benchmark.is_synthetic,
            }
        except InsufficientDataError:
            bench_prices = None

    report: dict[str, Any] = {
        "fund": {"id": fund.id, "name": fund.name, "scheme_code": fund.scheme_code,
                 "is_synthetic": fund.is_synthetic, "source": fund.source.name},
        "benchmark": bench_info,
        "period": _period_block(period, start, end, prices, latest, all_prices.index[0].date()),
        "freshness": series.freshness(latest, settings.stale_after_days).as_dict(),
        "assumptions": assumptions(conv, fund.return_basis),
        "metrics": risk_metrics_block(prices, bench_prices, conv),
    }

    if include_series:
        returns = metrics.simple_returns(prices)
        rebased = prices / prices.iloc[0] * 100
        report["series"] = {
            "nav": _series_points(prices, 4),
            "rebased": _series_points(rebased, 3),
            "drawdown": _series_points(metrics.drawdown_series(prices)),
        }
        if bench_prices is not None:
            aligned_bench = bench_prices.loc[bench_prices.index >= prices.index[0]]
            report["series"]["benchmark_rebased"] = _series_points(
                aligned_bench / aligned_bench.iloc[0] * 100, 3
            )
        rs, reason = _attempt(lambda: metrics.rolling_sharpe(returns, ROLLING_WINDOW, conv))
        report["series"]["rolling_sharpe"] = _series_points(rs, 4) if rs is not None else []
        rv, _ = _attempt(lambda: metrics.rolling_volatility(returns, 63, conv.periods_per_year))
        report["series"]["rolling_volatility"] = _series_points(rv, 5) if rv is not None else []
        if bench_prices is not None:
            rb, _ = _attempt(lambda: metrics.rolling_beta(
                returns, metrics.simple_returns(bench_prices), ROLLING_WINDOW))
            report["series"]["rolling_beta"] = _series_points(rb, 4) if rb is not None else []
        report["series"]["rolling_window_days"] = ROLLING_WINDOW
        report["series"]["return_distribution"] = return_histogram(returns)
        if reason:
            report["series"]["rolling_sharpe_unavailable_reason"] = reason
    return report


def return_histogram(returns: pd.Series, bins: int = 40) -> list[dict]:
    values = np.asarray(returns, dtype=float)
    if len(values) < 2:
        return []
    counts, edges = np.histogram(values, bins=bins)
    return [
        {"bin_start": float(edges[i]), "bin_end": float(edges[i + 1]), "count": int(counts[i])}
        for i in range(len(counts))
    ]


def correlation_report(db: Session, fund_ids: list[int], start: date | None, end: date | None) -> dict:
    frame, alignment = series.load_nav_frame(db, fund_ids, start, end)
    returns = frame.pct_change().dropna()
    metrics.require_observations(returns, metrics.MIN_BETA_OBSERVATIONS, "Correlation")
    corr = returns.corr()
    return {
        "fund_ids": fund_ids,
        "matrix": [[round(float(corr.iloc[i, j]), 4) for j in range(len(fund_ids))]
                   for i in range(len(fund_ids))],
        "observations": int(len(returns)),
        "period": {"start": frame.index[0].date().isoformat(), "end": frame.index[-1].date().isoformat()},
        "alignment": alignment,
    }
