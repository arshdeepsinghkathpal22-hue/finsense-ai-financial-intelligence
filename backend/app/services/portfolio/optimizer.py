"""Markowitz mean-variance portfolio optimisation.

Objective (maximised)::

    U(w) = w' mu - (lambda / 2) * w' Sigma w

subject to ``sum(w) = 1`` and ``min_weight <= w_i <= max_weight`` (long-only
by default). ``mu`` and ``Sigma`` are *historical* annualised estimates:
the optimiser finds the best allocation for the past, which is not a
forecast of future returns.

Covariance matrices estimated from a short or highly collinear history can
be singular or badly conditioned; in that case Ledoit-Wolf shrinkage is
applied and reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

from app.core.errors import InfeasibleError, ValidationFailedError
from app.services.analytics import metrics

WEIGHT_TOLERANCE = 1e-6
CONDITION_LIMIT = 1e8

RISK_PROFILES = {"conservative": 8.0, "moderate": 4.0, "aggressive": 1.5}


@dataclass
class Estimates:
    expected_returns: np.ndarray  # annualised
    covariance: np.ndarray  # annualised
    shrinkage_applied: bool = False
    shrinkage_intensity: float | None = None
    notes: list[str] = field(default_factory=list)


def estimate_inputs(returns: np.ndarray, periods_per_year: int, *, force_shrinkage: bool = False) -> Estimates:
    """Annualised mean vector and covariance from a (T x N) matrix of returns."""
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2 or returns.shape[1] < 1:
        raise ValidationFailedError("Returns must be a two-dimensional matrix (dates x assets).")
    if np.isnan(returns).any():
        raise ValidationFailedError("Returns contain missing observations; align dates first.")
    t, n = returns.shape
    if t < max(60, 2 * n):
        raise ValidationFailedError(
            f"At least {max(60, 2 * n)} overlapping observations are needed for {n} assets; got {t}."
        )
    mu = returns.mean(axis=0) * periods_per_year
    sample_cov = np.cov(returns, rowvar=False, ddof=1).reshape(n, n) * periods_per_year
    estimates = Estimates(mu, sample_cov)
    eigenvalues = np.linalg.eigvalsh(sample_cov)
    ill_conditioned = eigenvalues.min() <= 1e-12 or eigenvalues.max() / max(eigenvalues.min(), 1e-300) > CONDITION_LIMIT
    if force_shrinkage or ill_conditioned:
        lw = LedoitWolf().fit(returns)
        estimates.covariance = lw.covariance_ * periods_per_year
        estimates.shrinkage_applied = True
        estimates.shrinkage_intensity = float(lw.shrinkage_)
        if ill_conditioned:
            estimates.notes.append(
                "The sample covariance matrix was singular or ill-conditioned (assets move almost "
                "identically), so Ledoit-Wolf shrinkage was applied."
            )
    return estimates


def validate_covariance(cov: np.ndarray) -> None:
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise ValidationFailedError("Covariance matrix must be square.")
    if not np.all(np.isfinite(cov)):
        raise ValidationFailedError("Covariance matrix contains non-finite values.")
    if not np.allclose(cov, cov.T, atol=1e-10):
        raise ValidationFailedError("Covariance matrix must be symmetric.")
    if np.linalg.eigvalsh(cov).min() < -1e-10:
        raise ValidationFailedError("Covariance matrix must be positive semi-definite.")


def check_bounds_feasible(n_assets: int, min_weight: float, max_weight: float) -> None:
    if not 0 <= min_weight <= max_weight <= 1:
        raise InfeasibleError(
            "Weight limits must satisfy 0 <= minimum <= maximum <= 100%.",
            {"min_weight": min_weight, "max_weight": max_weight},
        )
    if n_assets * max_weight < 1 - WEIGHT_TOLERANCE:
        raise InfeasibleError(
            f"With {n_assets} assets and a maximum weight of {max_weight:.0%}, the weights can add "
            f"up to at most {n_assets * max_weight:.0%}. Raise the maximum weight to at least "
            f"{1 / n_assets:.1%} or add assets.",
            {"n_assets": n_assets, "max_weight": max_weight},
        )
    if n_assets * min_weight > 1 + WEIGHT_TOLERANCE:
        raise InfeasibleError(
            f"With {n_assets} assets and a minimum weight of {min_weight:.0%}, the weights would add "
            f"up to {n_assets * min_weight:.0%}, more than 100%. Lower the minimum weight to at most "
            f"{1 / n_assets:.1%}.",
            {"n_assets": n_assets, "min_weight": min_weight},
        )


@dataclass
class OptimisationResult:
    weights: np.ndarray
    expected_return: float
    volatility: float
    sharpe: float | None
    objective: str
    iterations: int


def _portfolio_stats(w: np.ndarray, est: Estimates, rf: float) -> tuple[float, float, float | None]:
    ret = metrics.portfolio_expected_return(w, est.expected_returns)
    vol = metrics.portfolio_volatility(w, est.covariance)
    sharpe = (ret - rf) / vol if vol > 0 else None
    return ret, vol, sharpe


def _solve(
    objective, est: Estimates, min_weight: float, max_weight: float,
    extra_constraints: list[dict] | None = None, start: np.ndarray | None = None,
):  # type: ignore[no-untyped-def]
    n = len(est.expected_returns)
    x0 = np.full(n, 1.0 / n) if start is None else start
    x0 = np.clip(x0, min_weight, max_weight)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    constraints += extra_constraints or []
    result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=[(min_weight, max_weight)] * n,
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-12},
    )
    weights = np.asarray(result.x, dtype=float)
    # Convergence checks: report failure rather than return arbitrary weights.
    if not result.success:
        raise InfeasibleError(
            f"The optimiser did not converge: {result.message}",
            {"iterations": int(result.nit)},
        )
    if abs(weights.sum() - 1.0) > 1e-4 or weights.min() < min_weight - 1e-4 or weights.max() > max_weight + 1e-4:
        raise InfeasibleError("The optimiser returned weights that violate the constraints.")
    weights = np.clip(weights, 0.0, None)
    weights[weights < 1e-6] = 0.0
    return weights / weights.sum(), int(result.nit)


def optimise_mean_variance(
    est: Estimates, *, risk_aversion: float, min_weight: float = 0.0, max_weight: float = 1.0,
    risk_free_rate: float = 0.0,
) -> OptimisationResult:
    validate_covariance(est.covariance)
    if risk_aversion <= 0:
        raise ValidationFailedError("Risk aversion must be positive.")
    n = len(est.expected_returns)
    check_bounds_feasible(n, min_weight, max_weight)
    mu, cov = est.expected_returns, est.covariance

    def negative_utility(w: np.ndarray) -> float:
        return -(w @ mu - 0.5 * risk_aversion * (w @ cov @ w))

    weights, nit = _solve(negative_utility, est, min_weight, max_weight)
    ret, vol, sharpe = _portfolio_stats(weights, est, risk_free_rate)
    return OptimisationResult(weights, ret, vol, sharpe, "mean_variance_utility", nit)


def minimum_variance(est: Estimates, *, min_weight: float = 0.0, max_weight: float = 1.0,
                     risk_free_rate: float = 0.0) -> OptimisationResult:
    validate_covariance(est.covariance)
    check_bounds_feasible(len(est.expected_returns), min_weight, max_weight)
    weights, nit = _solve(lambda w: w @ est.covariance @ w, est, min_weight, max_weight)
    ret, vol, sharpe = _portfolio_stats(weights, est, risk_free_rate)
    return OptimisationResult(weights, ret, vol, sharpe, "minimum_variance", nit)


def maximum_sharpe(est: Estimates, *, min_weight: float = 0.0, max_weight: float = 1.0,
                   risk_free_rate: float = 0.0) -> OptimisationResult:
    validate_covariance(est.covariance)
    check_bounds_feasible(len(est.expected_returns), min_weight, max_weight)

    def negative_sharpe(w: np.ndarray) -> float:
        vol = np.sqrt(max(w @ est.covariance @ w, 1e-16))
        return -((w @ est.expected_returns - risk_free_rate) / vol)

    weights, nit = _solve(negative_sharpe, est, min_weight, max_weight)
    ret, vol, sharpe = _portfolio_stats(weights, est, risk_free_rate)
    return OptimisationResult(weights, ret, vol, sharpe, "maximum_sharpe", nit)


def efficient_frontier(
    est: Estimates, *, min_weight: float = 0.0, max_weight: float = 1.0, points: int = 25,
    risk_free_rate: float = 0.0,
) -> list[dict]:
    """Minimum-variance portfolios for a grid of target returns."""
    validate_covariance(est.covariance)
    n = len(est.expected_returns)
    check_bounds_feasible(n, min_weight, max_weight)
    low = minimum_variance(est, min_weight=min_weight, max_weight=max_weight).expected_return
    # Highest achievable return under the bounds: fill the best assets first.
    order = np.argsort(est.expected_returns)[::-1]
    w = np.full(n, min_weight)
    remaining = 1.0 - w.sum()
    for idx in order:
        add = min(max_weight - w[idx], remaining)
        w[idx] += add
        remaining -= add
    high = float(w @ est.expected_returns)
    frontier = []
    previous = None
    for target in np.linspace(low, high, points):
        constraint = {"type": "eq", "fun": lambda w, t=target: w @ est.expected_returns - t}
        try:
            weights, _ = _solve(lambda w: w @ est.covariance @ w, est, min_weight, max_weight,
                                [constraint], start=previous)
        except InfeasibleError:
            continue
        previous = weights
        ret, vol, sharpe = _portfolio_stats(weights, est, risk_free_rate)
        frontier.append({"expected_return": ret, "volatility": vol, "sharpe": sharpe,
                         "weights": [round(float(x), 6) for x in weights]})
    return frontier
