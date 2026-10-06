"""Financial formulas checked against hand-computed results."""

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app.core.errors import InsufficientDataError
from app.services.analytics import metrics
from app.services.analytics.metrics import Conventions

ZERO_RF = Conventions(periods_per_year=252, risk_free_rate=0.0)


def prices(values, start="2024-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def returns_series(values):
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values)), dtype=float)


def test_simple_and_cumulative_returns():
    p = prices([100, 110, 99])
    assert metrics.simple_returns(p).tolist() == pytest.approx([0.10, -0.10])
    assert metrics.cumulative_return(p) == pytest.approx(-0.01)


def test_cagr_uses_calendar_time():
    p = pd.Series([100.0, 121.0], index=pd.to_datetime(["2020-01-01", "2022-01-01"]))
    years = (pd.Timestamp("2022-01-01") - pd.Timestamp("2020-01-01")).days / 365.25
    assert metrics.cagr(p) == pytest.approx(1.21 ** (1 / years) - 1)
    assert metrics.cagr(p) == pytest.approx(0.0999, abs=2e-4)


def test_per_period_rate_compounds_back_to_annual():
    daily = metrics.per_period_rate(0.065, 252)
    assert (1 + daily) ** 252 - 1 == pytest.approx(0.065)


def test_annualised_volatility_known_value():
    r = returns_series([0.01, -0.01] * 20)
    expected = math.sqrt(40 / 39) * 0.01 * math.sqrt(252)
    assert metrics.annualized_volatility(r, 252) == pytest.approx(expected)


def test_sharpe_matches_formula_and_zero_variance_is_none():
    r = returns_series([0.002, 0.001, -0.0005, 0.0015] * 10)
    expected = r.mean() / r.std(ddof=1) * math.sqrt(252)
    assert metrics.sharpe_ratio(r, ZERO_RF) == pytest.approx(expected)
    assert metrics.sharpe_ratio(returns_series([0.001] * 40), ZERO_RF) is None


def test_sharpe_subtracts_risk_free_rate():
    r = returns_series([0.002, 0.001, -0.0005, 0.0015] * 10)
    conv = Conventions(risk_free_rate=0.05)
    excess = r - conv.per_period_rf()
    assert metrics.sharpe_ratio(r, conv) == pytest.approx(excess.mean() / excess.std(ddof=1) * math.sqrt(252))


def test_sortino_known_value():
    r = returns_series([0.02, -0.01] * 20)
    # mean 0.005; downside deviation sqrt(mean(min(r,0)^2)) = sqrt(0.5 * 0.0001)
    expected = 0.005 / math.sqrt(0.5 * 0.0001) * math.sqrt(252)
    assert metrics.sortino_ratio(r, ZERO_RF) == pytest.approx(expected)
    assert metrics.sortino_ratio(returns_series([0.01] * 40), ZERO_RF) is None


def test_max_drawdown_dates_and_depth():
    p = prices([100, 120, 90, 95, 130])
    mdd = metrics.max_drawdown(p)
    assert mdd.depth == pytest.approx(-0.25)
    assert mdd.peak_date == p.index[1].date()
    assert mdd.trough_date == p.index[2].date()
    assert mdd.recovery_date == p.index[4].date()


def test_drawdown_without_recovery():
    p = prices([100, 80, 90])
    mdd = metrics.max_drawdown(p)
    assert mdd.depth == pytest.approx(-0.2)
    assert mdd.recovery_date is None
    assert metrics.drawdown_series(p).tolist() == pytest.approx([0.0, -0.2, -0.1])


def test_historical_var_and_cvar_known_values():
    r = returns_series(np.linspace(-0.05, 0.05, 101))
    # 5th percentile with linear interpolation is exactly -0.045
    assert metrics.historical_var(r, 0.95) == pytest.approx(0.045)
    # mean of the six returns at or below -0.045
    assert metrics.historical_cvar(r, 0.95) == pytest.approx(0.0475)


def test_parametric_var_matches_normal_quantile():
    r = returns_series([0.01, -0.01, 0.02, -0.02, 0.0] * 8)
    mu, sigma = r.mean(), r.std(ddof=1)
    assert metrics.parametric_var(r, 0.99) == pytest.approx(-(mu + stats.norm.ppf(0.01) * sigma))
    assert metrics.parametric_cvar(r, 0.95) > metrics.parametric_var(r, 0.95)


def test_var_rejects_bad_confidence():
    with pytest.raises(ValueError):
        metrics.historical_var(returns_series([0.01] * 40), 1.2)


def test_beta_correlation_and_alignment():
    bench = returns_series(np.sin(np.arange(80)) / 100)
    asset = 2 * bench
    stats_ = metrics.benchmark_statistics(asset, bench, ZERO_RF)
    assert stats_.beta == pytest.approx(2.0)
    assert stats_.correlation == pytest.approx(1.0)
    # alignment: dates present in only one series are ignored
    asset_shifted = asset.iloc[5:]
    assert metrics.benchmark_statistics(asset_shifted, bench, ZERO_RF).observations == 75


def test_insufficient_observations_raise():
    with pytest.raises(InsufficientDataError):
        metrics.annualized_volatility(returns_series([0.01] * 5), 252)
    with pytest.raises(InsufficientDataError):
        metrics.benchmark_statistics(returns_series([0.01] * 20), returns_series([0.01] * 20), ZERO_RF)


@pytest.mark.parametrize("bad", [[100, -5, 101], [100, float("nan"), 101], [0, 100, 101]])
def test_invalid_price_series_rejected(bad):
    with pytest.raises(ValueError):
        metrics.simple_returns(prices(bad))


def test_duplicate_dates_rejected():
    p = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-01", "2024-01-01"]))
    with pytest.raises(ValueError):
        metrics.validate_price_series(p)


def test_concentration_measures():
    assert metrics.herfindahl_index([0.25] * 4) == pytest.approx(0.25)
    assert metrics.effective_number_of_holdings([0.25] * 4) == pytest.approx(4)
    assert metrics.herfindahl_index([1.0]) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        metrics.normalise_weights([0.5, -0.5])


def test_portfolio_volatility_and_diversification():
    w = np.array([0.5, 0.5])
    cov_uncorrelated = np.diag([0.1**2, 0.2**2])
    assert metrics.portfolio_volatility(w, cov_uncorrelated) == pytest.approx(math.sqrt(0.0125))
    cov_perfect = np.array([[0.01, 0.02], [0.02, 0.04]])
    assert metrics.diversification_ratio(w, cov_perfect) == pytest.approx(1.0)
    assert metrics.diversification_ratio(w, cov_uncorrelated) > 1.0


def test_buy_and_hold_value_and_drift():
    frame = pd.DataFrame({"a": [10.0, 20.0], "b": [10.0, 10.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    value = metrics.buy_and_hold_value(frame, np.array([0.5, 0.5]), 1000.0)
    assert value.tolist() == pytest.approx([1000.0, 1500.0])
    assert metrics.current_weights(frame, np.array([0.5, 0.5])).tolist() == pytest.approx([2 / 3, 1 / 3])


def test_rolling_metrics_lengths():
    r = returns_series(np.random.default_rng(1).normal(0, 0.01, 200))
    assert len(metrics.rolling_sharpe(r, 63, ZERO_RF)) == 200 - 62
    assert len(metrics.rolling_volatility(r, 63, 252)) == 200 - 62
