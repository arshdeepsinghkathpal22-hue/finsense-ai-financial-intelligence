"""Portfolio optimisation and what-if scenario calculations."""

import math

import numpy as np
import pytest

from app.core.errors import InfeasibleError, ValidationFailedError
from app.services import simulation
from app.services.portfolio import optimizer
from app.services.portfolio.optimizer import Estimates


def est(mu, vols, corr=None):
    vols = np.asarray(vols, dtype=float)
    corr = np.eye(len(vols)) if corr is None else np.asarray(corr, dtype=float)
    return Estimates(np.asarray(mu, dtype=float), np.outer(vols, vols) * corr)


def assert_valid(weights, low=0.0, high=1.0):
    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert weights.min() >= low - 1e-6
    assert weights.max() <= high + 1e-6


def test_minimum_variance_two_uncorrelated_assets():
    # w1 = (1/s1^2) / (1/s1^2 + 1/s2^2) = 100 / 125
    result = optimizer.minimum_variance(est([0.1, 0.15], [0.1, 0.2]))
    assert result.weights[0] == pytest.approx(0.8, abs=1e-4)
    assert_valid(result.weights)


def test_maximum_sharpe_matches_tangency_portfolio():
    # Uncorrelated assets: w ∝ (mu - rf) / sigma^2 = [10, 3.75]
    result = optimizer.maximum_sharpe(est([0.1, 0.15], [0.1, 0.2]), risk_free_rate=0.0)
    assert result.weights[0] == pytest.approx(10 / 13.75, abs=1e-3)


def test_higher_risk_aversion_lowers_volatility():
    e = est([0.06, 0.10, 0.14], [0.05, 0.15, 0.25])
    vols = [optimizer.optimise_mean_variance(e, risk_aversion=ra).volatility for ra in (1, 4, 16)]
    assert vols[0] >= vols[1] >= vols[2]


def test_bounds_are_respected():
    e = est([0.06, 0.10, 0.14], [0.05, 0.15, 0.25])
    result = optimizer.optimise_mean_variance(e, risk_aversion=1, min_weight=0.1, max_weight=0.5)
    assert_valid(result.weights, 0.1, 0.5)


@pytest.mark.parametrize("n_assets,low,high,hint", [
    (4, 0.0, 0.2, "Raise the maximum weight"),   # weights can reach at most 80%
    (3, 0.4, 1.0, "Lower the minimum weight"),    # minimums alone exceed 100%
    (3, 0.6, 0.5, "minimum <= maximum"),
])
def test_infeasible_bounds_explained(n_assets, low, high, hint):
    with pytest.raises(InfeasibleError) as exc:
        optimizer.check_bounds_feasible(n_assets, low, high)
    assert hint in exc.value.message


def test_singular_covariance_triggers_shrinkage():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, 300)
    returns = np.column_stack([base, base, rng.normal(0, 0.01, 300)])  # two identical assets
    estimates = optimizer.estimate_inputs(returns, 252)
    assert estimates.shrinkage_applied
    assert estimates.notes
    result = optimizer.minimum_variance(estimates)
    assert_valid(result.weights)


def test_non_psd_covariance_rejected():
    bad = Estimates(np.array([0.1, 0.1]), np.array([[0.01, 0.05], [0.05, 0.01]]))
    with pytest.raises(ValidationFailedError):
        optimizer.optimise_mean_variance(bad, risk_aversion=2)


def test_estimate_inputs_needs_enough_observations():
    with pytest.raises(ValidationFailedError):
        optimizer.estimate_inputs(np.zeros((20, 3)), 252)
    with pytest.raises(ValidationFailedError):
        optimizer.estimate_inputs(np.full((100, 2), np.nan), 252)


def test_efficient_frontier_is_increasing_in_return():
    frontier = optimizer.efficient_frontier(est([0.06, 0.10, 0.14], [0.05, 0.15, 0.25]), points=10)
    assert len(frontier) >= 8
    returns = [p["expected_return"] for p in frontier]
    assert returns == sorted(returns)
    for point in frontier:
        assert sum(point["weights"]) == pytest.approx(1.0, abs=1e-4)


def test_sip_zero_return_equals_contributions():
    result = simulation.sip_projection(monthly_amount=1000, years=2, annual_return=0.0)
    assert result["final_value"] == pytest.approx(24000)
    assert result["gain"] == pytest.approx(0)


def test_sip_matches_annuity_due_formula():
    i = 1.12 ** (1 / 12) - 1
    expected = 1000 * ((1 + i) ** 12 - 1) / i * (1 + i)
    result = simulation.sip_projection(monthly_amount=1000, years=1, annual_return=0.12)
    assert result["final_value"] == pytest.approx(expected, abs=0.01)


def test_lumpsum_compounds_at_the_annual_rate():
    result = simulation.sip_projection(monthly_amount=0, years=3, annual_return=0.10, lumpsum=1000)
    assert result["final_value"] == pytest.approx(1000 * 1.1**3, abs=0.01)


def test_monte_carlo_is_reproducible_and_ordered():
    args = {"monthly_amount": 5000, "years": 5, "annual_return": 0.1, "annual_volatility": 0.15, "paths": 500}
    a, b = simulation.monte_carlo_sip(**args, seed=3), simulation.monte_carlo_sip(**args, seed=3)
    assert a["final_percentiles"] == b["final_percentiles"]
    p = a["final_percentiles"]
    assert p["p5"] < p["p25"] < p["p50"] < p["p75"] < p["p95"]
    zero_vol = simulation.monte_carlo_sip(**{**args, "annual_volatility": 0.0}, seed=1)
    assert zero_vol["final_percentiles"]["p50"] == pytest.approx(zero_vol["deterministic_reference"], rel=1e-6)


def test_market_shock_is_weighted_beta():
    result = simulation.market_shock(weights=[0.5, 0.5], betas=[1.2, 0.2], labels=["A", "B"],
                                     market_decline=-0.2, portfolio_value=100000)
    assert result["estimated_portfolio_return"] == pytest.approx(0.5 * 1.2 * -0.2 + 0.5 * 0.2 * -0.2)
    assert result["estimated_value_after"] == pytest.approx(100000 * (1 - 0.14))
    with pytest.raises(ValidationFailedError):
        simulation.market_shock(weights=[1], betas=[None], labels=["A"], market_decline=-0.1, portfolio_value=1)


def test_stress_with_perfect_correlation():
    result = simulation.assumption_stress(weights=[0.5, 0.5], expected_returns=[0.1, 0.1], volatilities=[0.1, 0.2],
                                          correlation=[[1, 0], [0, 1]], correlation_override=1.0)
    assert result["scenario"]["volatility"] == pytest.approx(0.15)
    assert result["baseline"]["volatility"] == pytest.approx(math.sqrt(0.0125))


def test_stress_rejects_inconsistent_correlations():
    with pytest.raises(ValidationFailedError):
        simulation.assumption_stress(weights=[1 / 3] * 3, expected_returns=[0.1] * 3, volatilities=[0.1] * 3,
                                     correlation=[[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]])
