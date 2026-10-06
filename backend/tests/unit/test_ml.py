"""Feature preparation, leakage prevention, model evaluation and anomaly detection."""

import math

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InsufficientDataError, ValidationFailedError
from app.services.ml import anomalies, features, forecasting


def synthetic_nav(n=1200, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(start * np.cumprod(1 + rng.normal(0.0004, 0.01, n)), index=idx)


def test_features_do_not_look_ahead():
    nav = synthetic_nav()
    full = features.build_features(nav)
    cut = nav.index[800]
    truncated = features.build_features(nav.loc[:cut])
    # Every feature value at `cut` must be identical whether or not later data exists.
    pd.testing.assert_series_equal(full.loc[cut], truncated.loc[cut], check_names=False)


def test_target_is_forward_log_return():
    nav = synthetic_nav()
    target = features.build_target(nav, 21)
    assert target.iloc[10] == pytest.approx(math.log(nav.iloc[31] / nav.iloc[10]))
    assert target.iloc[-21:].isna().all()


def test_dataset_excludes_rows_without_known_target():
    nav = synthetic_nav()
    X, y, latest = features.make_dataset(nav, None, 21)
    assert len(X) == len(y)
    assert X.index.max() < latest.index[0]
    assert latest.index[0] == nav.index[-1]


def test_chronological_split_has_purge_gaps():
    split = features.chronological_split(1000, horizon=21)
    (_, train_end), (val_start, val_end), (test_start, _) = split["train"], split["val"], split["test"]
    assert val_start - train_end == 21
    assert test_start - val_end == 21
    with pytest.raises(InsufficientDataError):
        features.chronological_split(250, horizon=63)


def test_short_history_is_rejected():
    with pytest.raises(InsufficientDataError):
        features.build_features(synthetic_nav(n=200))


def test_regression_metrics():
    m = forecasting.regression_metrics(np.array([0.01, -0.02, 0.03]), np.array([0.02, -0.01, -0.01]))
    assert m["mae"] == pytest.approx((0.01 + 0.01 + 0.04) / 3)
    assert m["rmse"] == pytest.approx(math.sqrt((0.0001 + 0.0001 + 0.0016) / 3))
    assert m["directional_accuracy"] == pytest.approx(2 / 3)
    # A predictor that never commits to a direction has no directional accuracy.
    assert forecasting.regression_metrics(np.array([0.01, -0.02]), np.zeros(2))["directional_accuracy"] is None


@pytest.mark.parametrize("model_type", ["historical_mean", "ridge", "random_forest"])
def test_training_reports_test_metrics_and_baselines(model_type):
    nav = synthetic_nav()
    trained = forecasting.train(nav, None, model_type=model_type, horizon=21)
    for key in ("test", "baseline_historical_mean", "baseline_zero_return"):
        assert trained.metrics[key]["mae"] >= 0
    assert 0 <= trained.metrics["test"]["interval_coverage_80"] <= 1
    assert trained.split_dates["train"][1] < trained.split_dates["validation"][0] < trained.split_dates["test"][0]
    latest = forecasting.forecast_latest(trained, nav, None)
    band = latest["interval_80"]
    assert band["lower_nav"] <= band["upper_nav"]
    assert latest["as_of"] == nav.index[-1].date().isoformat()


def test_unknown_horizon_rejected():
    with pytest.raises(ValueError):
        forecasting.train(synthetic_nav(), None, model_type="ridge", horizon=7)


def test_shap_explanation_for_tree_model():
    nav = synthetic_nav()
    trained = forecasting.train(nav, None, model_type="random_forest", horizon=5)
    explanation = forecasting.explain(trained, nav, None)
    assert explanation["available"]
    assert {row["feature"] for row in explanation["local"]} == set(trained.feature_names)
    baseline = forecasting.train(nav, None, model_type="historical_mean", horizon=5)
    assert forecasting.explain(baseline, nav, None)["available"] is False


def test_anomaly_detection_finds_planted_spike():
    nav = synthetic_nav(n=800, seed=3)
    returns = nav.pct_change().fillna(0)
    spike_day = nav.index[500]
    returns.loc[spike_day] = -0.12
    shocked = 100 * (1 + returns).cumprod()
    result = anomalies.detect(shocked, None, contamination=0.01)
    assert spike_day.date().isoformat() in {a["date"] for a in result["anomalies"]}
    flagged = next(a for a in result["anomalies"] if a["date"] == spike_day.date().isoformat())
    assert flagged["drivers"] and flagged["interpretation"]


def test_anomaly_input_validation():
    with pytest.raises(ValidationFailedError):
        anomalies.detect(synthetic_nav(), None, contamination=0.5)
    with pytest.raises(InsufficientDataError):
        anomalies.detect(synthetic_nav(n=150), None, contamination=0.01)
