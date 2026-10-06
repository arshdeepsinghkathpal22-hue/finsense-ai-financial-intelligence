"""Financial performance and risk calculations.

Conventions used throughout (and reported to users next to each figure):

* Inputs are NAV or index-level series observed on trading days. Returns
  are simple period returns ``r_t = P_t / P_{t-1} - 1`` between consecutive
  observations; a gap over a holiday is treated as one period.
* Annualisation uses ``periods_per_year`` (252 for daily data). Volatility
  scales with the square root of that factor, mean returns linearly.
* The risk-free rate is an *annual* decimal rate converted to a per-period
  rate geometrically: ``(1 + rf) ** (1 / periods_per_year) - 1``.
* VaR and CVaR are one-period (one trading day) figures, expressed as a
  positive loss fraction. Historical VaR is the empirical quantile of
  returns using linear interpolation between order statistics (NumPy's
  default); CVaR is the mean of returns at or below the VaR threshold.
  Other estimators (nearest-rank quantiles, kernel smoothing, Cornish-Fisher
  adjustments) give somewhat different numbers on the same data.
* Return figures are NAV-based. For growth options that equals a total
  return net of fund expenses; for income-distribution (IDCW) options it is
  a price return and understates investor returns. Dividends are never
  imputed.

All functions are pure: they take pandas/NumPy inputs and return plain
Python values, raising :class:`InsufficientDataError` or ``ValueError``
rather than returning made-up numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats

from app.core.errors import InsufficientDataError

MIN_OBSERVATIONS = 30  # minimum returns for volatility-type statistics
MIN_BETA_OBSERVATIONS = 60  # minimum overlapping returns for beta / correlation


@dataclass(frozen=True)
class Conventions:
    periods_per_year: int = 252
    risk_free_rate: float = 0.065
    var_confidence: float = 0.95

    def per_period_rf(self) -> float:
        return per_period_rate(self.risk_free_rate, self.periods_per_year)


# --------------------------------------------------------------------------- validation


def validate_price_series(prices: pd.Series, *, name: str = "series") -> pd.Series:
    """Checks a level series (NAV / index) and returns it sorted by date as floats."""
    if not isinstance(prices, pd.Series):
        raise TypeError(f"{name} must be a pandas Series")
    if prices.empty:
        raise InsufficientDataError(f"{name} has no observations.")
    series = prices.astype(float).sort_index()
    if series.index.has_duplicates:
        raise ValueError(f"{name} contains duplicate dates.")
    if series.isna().any():
        raise ValueError(f"{name} contains missing values; clean or drop them before analysis.")
    if (series <= 0).any():
        raise ValueError(f"{name} contains non-positive levels, which are invalid for NAV/index data.")
    return series


def require_observations(returns: pd.Series | np.ndarray, minimum: int, what: str) -> None:
    count = len(returns)
    if count < minimum:
        raise InsufficientDataError(
            f"{what} needs at least {minimum} return observations; only {count} available.",
            {"required": minimum, "available": count},
        )


# --------------------------------------------------------------------------- returns


def per_period_rate(annual_rate: float, periods_per_year: int) -> float:
    return (1.0 + annual_rate) ** (1.0 / periods_per_year) - 1.0


def simple_returns(prices: pd.Series) -> pd.Series:
    series = validate_price_series(prices, name="prices")
    return series.pct_change().dropna()


def cumulative_return(prices: pd.Series) -> float:
    series = validate_price_series(prices, name="prices")
    if len(series) < 2:
        raise InsufficientDataError("Cumulative return needs at least two observations.")
    return float(series.iloc[-1] / series.iloc[0] - 1.0)


def cagr(prices: pd.Series) -> float:
    """Compound annual growth rate using calendar time between first and last date."""
    series = validate_price_series(prices, name="prices")
    if len(series) < 2:
        raise InsufficientDataError("CAGR needs at least two observations.")
    start, end = pd.Timestamp(series.index[0]), pd.Timestamp(series.index[-1])
    years = (end - start).days / 365.25
    if years <= 0:
        raise InsufficientDataError("CAGR needs observations spanning a positive period.")
    return float((series.iloc[-1] / series.iloc[0]) ** (1.0 / years) - 1.0)


def annualized_mean_return(returns: pd.Series, periods_per_year: int) -> float:
    require_observations(returns, 2, "Annualised mean return")
    return float(np.mean(returns) * periods_per_year)


def annualized_volatility(returns: pd.Series, periods_per_year: int) -> float:
    require_observations(returns, MIN_OBSERVATIONS, "Volatility")
    return float(np.std(returns, ddof=1) * math.sqrt(periods_per_year))


# --------------------------------------------------------------------------- risk-adjusted


def sharpe_ratio(returns: pd.Series, conventions: Conventions) -> float | None:
    """Annualised Sharpe ratio; ``None`` when returns have zero variance."""
    require_observations(returns, MIN_OBSERVATIONS, "Sharpe ratio")
    excess = np.asarray(returns, dtype=float) - conventions.per_period_rf()
    sd = np.std(excess, ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return None
    return float(np.mean(excess) / sd * math.sqrt(conventions.periods_per_year))


def sortino_ratio(returns: pd.Series, conventions: Conventions) -> float | None:
    """Annualised Sortino ratio with the risk-free rate as the target return.

    Downside deviation is ``sqrt(mean(min(r - target, 0)^2))`` over *all*
    periods (not only the losing ones). ``None`` when there is no downside.
    """
    require_observations(returns, MIN_OBSERVATIONS, "Sortino ratio")
    excess = np.asarray(returns, dtype=float) - conventions.per_period_rf()
    downside = np.sqrt(np.mean(np.minimum(excess, 0.0) ** 2))
    if downside == 0:
        return None
    return float(np.mean(excess) / downside * math.sqrt(conventions.periods_per_year))


@dataclass(frozen=True)
class BenchmarkStats:
    beta: float
    correlation: float
    alpha_annual: float
    tracking_error: float
    information_ratio: float | None
    observations: int


def align_returns(*series: pd.Series) -> pd.DataFrame:
    """Inner-joins return series on date so every row has all observations."""
    frame = pd.concat(series, axis=1, join="inner")
    return frame.dropna()


def benchmark_statistics(
    asset_returns: pd.Series, benchmark_returns: pd.Series, conventions: Conventions
) -> BenchmarkStats:
    """Beta, correlation, Jensen's alpha and tracking error on date-aligned returns."""
    aligned = align_returns(asset_returns.rename("asset"), benchmark_returns.rename("bench"))
    require_observations(aligned, MIN_BETA_OBSERVATIONS, "Beta")
    a = aligned["asset"].to_numpy()
    b = aligned["bench"].to_numpy()
    var_b = np.var(b, ddof=1)
    if var_b == 0:
        raise InsufficientDataError("Benchmark returns have zero variance; beta is undefined.")
    beta = float(np.cov(a, b, ddof=1)[0, 1] / var_b)
    corr = float(np.corrcoef(a, b)[0, 1])
    rf = conventions.per_period_rf()
    ppy = conventions.periods_per_year
    alpha = float(((a.mean() - rf) - beta * (b.mean() - rf)) * ppy)
    active = a - b
    te = float(np.std(active, ddof=1) * math.sqrt(ppy))
    ir = float(active.mean() * ppy / te) if te > 0 else None
    return BenchmarkStats(beta, corr, alpha, te, ir, len(aligned))


# --------------------------------------------------------------------------- drawdown


def drawdown_series(prices: pd.Series) -> pd.Series:
    """Percentage decline of each level from its running historical peak."""
    series = validate_price_series(prices, name="prices")
    wealth = series / series.iloc[0]
    return wealth / wealth.cummax() - 1.0


@dataclass(frozen=True)
class MaxDrawdown:
    depth: float  # negative fraction, e.g. -0.25
    peak_date: date
    trough_date: date
    recovery_date: date | None


def max_drawdown(prices: pd.Series) -> MaxDrawdown:
    series = validate_price_series(prices, name="prices")
    if len(series) < 2:
        raise InsufficientDataError("Maximum drawdown needs at least two observations.")
    dd = drawdown_series(series)
    trough = dd.idxmin()
    depth = float(dd.loc[trough])
    peak = series.loc[:trough].idxmax()
    recovered = series.loc[trough:][series.loc[trough:] >= series.loc[peak]]
    recovery = recovered.index[0] if depth < 0 and not recovered.empty else None
    return MaxDrawdown(
        depth=depth,
        peak_date=pd.Timestamp(peak).date(),
        trough_date=pd.Timestamp(trough).date(),
        recovery_date=pd.Timestamp(recovery).date() if recovery is not None else None,
    )


# --------------------------------------------------------------------------- VaR / CVaR


def _check_confidence(confidence: float) -> None:
    if not 0.5 < confidence < 1:
        raise ValueError("Confidence level must be between 0.5 and 1 (e.g. 0.95).")


def historical_var(returns: pd.Series, confidence: float = 0.95) -> float:
    """One-period historical VaR as a positive loss fraction."""
    _check_confidence(confidence)
    require_observations(returns, MIN_OBSERVATIONS, "Historical VaR")
    quantile = np.quantile(np.asarray(returns, dtype=float), 1.0 - confidence)
    return float(-quantile)


def historical_cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """Expected shortfall: mean loss on periods at or beyond the historical VaR."""
    var = historical_var(returns, confidence)
    values = np.asarray(returns, dtype=float)
    tail = values[values <= -var]
    return float(-tail.mean())


def parametric_var(returns: pd.Series, confidence: float = 0.95) -> float:
    """Gaussian (variance-covariance) VaR, a model estimate for comparison."""
    _check_confidence(confidence)
    require_observations(returns, MIN_OBSERVATIONS, "Parametric VaR")
    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))
    z = stats.norm.ppf(1.0 - confidence)
    return float(-(mu + z * sigma))


def parametric_cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    _check_confidence(confidence)
    require_observations(returns, MIN_OBSERVATIONS, "Parametric CVaR")
    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))
    z = stats.norm.ppf(1.0 - confidence)
    return float(-(mu - sigma * stats.norm.pdf(z) / (1.0 - confidence)))


# --------------------------------------------------------------------------- rolling


def rolling_volatility(returns: pd.Series, window: int, periods_per_year: int) -> pd.Series:
    require_observations(returns, window, "Rolling volatility")
    return (returns.rolling(window).std(ddof=1) * math.sqrt(periods_per_year)).dropna()


def rolling_sharpe(returns: pd.Series, window: int, conventions: Conventions) -> pd.Series:
    require_observations(returns, window, "Rolling Sharpe ratio")
    excess = returns - conventions.per_period_rf()
    mean = excess.rolling(window).mean()
    sd = excess.rolling(window).std(ddof=1)
    ratio = mean / sd.replace(0.0, np.nan) * math.sqrt(conventions.periods_per_year)
    return ratio.dropna()


def rolling_beta(asset_returns: pd.Series, benchmark_returns: pd.Series, window: int) -> pd.Series:
    aligned = align_returns(asset_returns.rename("a"), benchmark_returns.rename("b"))
    require_observations(aligned, window, "Rolling beta")
    cov = aligned["a"].rolling(window).cov(aligned["b"])
    var = aligned["b"].rolling(window).var()
    return (cov / var.replace(0.0, np.nan)).dropna()


# --------------------------------------------------------------------------- portfolios


def normalise_weights(weights: np.ndarray | list[float]) -> np.ndarray:
    w = np.asarray(weights, dtype=float)
    if w.ndim != 1 or w.size == 0:
        raise ValueError("Weights must be a non-empty vector.")
    if np.any(~np.isfinite(w)) or np.any(w < 0):
        raise ValueError("Weights must be finite and non-negative.")
    total = w.sum()
    if total <= 0:
        raise ValueError("Weights must sum to a positive number.")
    return w / total


def herfindahl_index(weights: np.ndarray | list[float]) -> float:
    """Sum of squared weights: 1/N for equal weights, 1 for a single holding."""
    w = normalise_weights(weights)
    return float(np.sum(w**2))


def effective_number_of_holdings(weights: np.ndarray | list[float]) -> float:
    return 1.0 / herfindahl_index(weights)


def portfolio_expected_return(weights: np.ndarray, expected_returns: np.ndarray) -> float:
    return float(np.dot(weights, expected_returns))


def portfolio_volatility(weights: np.ndarray, covariance: np.ndarray) -> float:
    variance = float(weights @ covariance @ weights)
    return math.sqrt(max(variance, 0.0))


def diversification_ratio(weights: np.ndarray, covariance: np.ndarray) -> float:
    """Weighted average asset volatility divided by portfolio volatility (>= 1)."""
    asset_vols = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    port_vol = portfolio_volatility(weights, covariance)
    if port_vol == 0:
        raise ValueError("Portfolio volatility is zero; diversification ratio is undefined.")
    return float(np.dot(weights, asset_vols) / port_vol)


def buy_and_hold_value(prices: pd.DataFrame, weights: np.ndarray, initial_value: float) -> pd.Series:
    """Value of a portfolio bought at the first date and never rebalanced.

    Each asset's holding grows with its own NAV, so weights drift over time,
    which is how a real investor's portfolio behaves without rebalancing.
    """
    if prices.isna().any().any():
        raise ValueError("Price frame must be date-aligned with no missing values.")
    w = normalise_weights(weights)
    growth = prices / prices.iloc[0]
    return (growth * (w * initial_value)).sum(axis=1)


def current_weights(prices: pd.DataFrame, initial_weights: np.ndarray) -> np.ndarray:
    """Drifted weights at the last date of a buy-and-hold portfolio."""
    growth = prices.iloc[-1] / prices.iloc[0]
    values = normalise_weights(initial_weights) * growth.to_numpy()
    return values / values.sum()
