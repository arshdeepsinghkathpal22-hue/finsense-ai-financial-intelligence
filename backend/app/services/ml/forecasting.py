"""NAV return forecasting with honest evaluation against baselines.

Models
------
* ``historical_mean`` - predicts the average training-period return (baseline)
* ``ridge``           - standardised ridge regression
* ``random_forest``   - random forest regressor

Procedure: chronological train / validation / test split with purge gaps;
hyper-parameters chosen on validation; the chosen model is refitted on
train + validation and evaluated once on the untouched test period. The
80% prediction interval comes from validation residual quantiles scaled by
trailing volatility, and its empirical coverage on the test period is
reported so users can see whether the stated uncertainty is realistic.

An LSTM was considered but is not offered: a single fund's daily NAV
history (a few thousand points) is too small for it to beat these
baselines reliably, and it would add a large deep-learning dependency.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from typing import Literal

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.services.ml import features as fx

ModelType = Literal["ridge", "random_forest", "historical_mean"]
MODEL_TYPES: tuple[str, ...] = ("ridge", "random_forest", "historical_mean")
HORIZONS = (5, 21, 63)
RANDOM_STATE = 42


class MeanModel:
    """Predicts a constant: the mean target of the data it was fitted on."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> MeanModel:  # noqa: N803
        self.mean_ = float(np.mean(y))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        return np.full(len(X), self.mean_)


def _candidates(model_type: str) -> list[tuple[dict, object]]:
    if model_type == "ridge":
        return [({"alpha": a}, Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=a))]))
                for a in (0.1, 1.0, 10.0, 100.0)]
    if model_type == "random_forest":
        return [({"max_depth": d, "n_estimators": 200, "min_samples_leaf": 20},
                 RandomForestRegressor(n_estimators=200, max_depth=d, min_samples_leaf=20,
                                       random_state=RANDOM_STATE, n_jobs=1))
                for d in (3, 5, 8)]
    if model_type == "historical_mean":
        return [({}, MeanModel())]
    raise ValueError(f"Unknown model type {model_type}")


def _clone(model: object) -> object:
    from sklearn.base import clone

    return MeanModel() if isinstance(model, MeanModel) else clone(model)


def regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    """MAE, RMSE and directional accuracy.

    Directional accuracy only counts forecasts that commit to a direction
    (non-zero prediction and non-zero outcome); for a predictor that always
    says "zero" it is undefined (None) rather than 0%.
    """
    errors = predicted - actual
    committed = (actual != 0) & (predicted != 0)
    directional = (
        float(np.mean(np.sign(predicted[committed]) == np.sign(actual[committed]))) if committed.any() else None
    )
    return {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(math.sqrt(np.mean(errors**2))),
        "directional_accuracy": directional,
        "observations": int(len(actual)),
    }


@dataclass
class TrainedForecast:
    model_type: str
    horizon: int
    model: object
    feature_names: list[str]
    hyperparameters: dict
    metrics: dict
    residual_quantiles: tuple[float, float]
    split_dates: dict
    backtest: list[dict]


def _vol_scale(X: pd.DataFrame, horizon: int) -> np.ndarray:  # noqa: N803
    """Expected horizon volatility from trailing 3-month annualised volatility."""
    return np.maximum(X["vol_63d"].to_numpy(), 1e-4) * math.sqrt(horizon / 252)


def data_fingerprint(fund_id: int, model_type: str, horizon: int, nav: pd.Series, has_benchmark: bool) -> str:
    raw = (f"{fx.FEATURE_VERSION}|{fund_id}|{model_type}|{horizon}|{len(nav)}|{nav.index[0].date()}|"
           f"{nav.index[-1].date()}|{nav.iloc[-1]:.6f}|{has_benchmark}")
    return hashlib.sha256(raw.encode()).hexdigest()


def train(nav: pd.Series, benchmark: pd.Series | None, *, model_type: str, horizon: int) -> TrainedForecast:
    if horizon not in HORIZONS:
        raise ValueError(f"Horizon must be one of {HORIZONS} trading days.")
    X, y, _ = fx.make_dataset(nav, benchmark, horizon)  # noqa: N806
    split = fx.chronological_split(len(X), horizon)
    (a, b), (c, d), (e, f) = split["train"], split["val"], split["test"]
    X_train, y_train = X.iloc[a:b], y.iloc[a:b]  # noqa: N806
    X_val, y_val = X.iloc[c:d], y.iloc[c:d]  # noqa: N806
    X_test, y_test = X.iloc[e:f], y.iloc[e:f]  # noqa: N806

    # 1. Choose hyper-parameters on the validation period.
    best = None
    for params, candidate in _candidates(model_type):
        model = _clone(candidate).fit(X_train, y_train)
        mae = float(np.mean(np.abs(model.predict(X_val) - y_val.to_numpy())))
        if best is None or mae < best[0]:
            best = (mae, params, candidate, model)
    assert best is not None
    _, params, template, fitted_on_train = best
    # Interval: quantiles of validation residuals *standardised by the
    # volatility known at prediction time*, then rescaled by the volatility
    # on the forecast date. Uncertainty therefore widens in turbulent markets.
    residuals = y_val.to_numpy() - fitted_on_train.predict(X_val)
    z = residuals / _vol_scale(X_val, horizon)
    q_low, q_high = (float(np.quantile(z, 0.10)), float(np.quantile(z, 0.90)))

    # 2. Refit on train + validation; evaluate once on the held-out test period.
    X_fit, y_fit = X.iloc[:d], y.iloc[:d]  # noqa: N806
    final = _clone(template).fit(X_fit, y_fit)
    predicted = final.predict(X_test)
    actual = y_test.to_numpy()
    model_metrics = regression_metrics(actual, predicted)
    baseline = MeanModel().fit(X_fit, y_fit)
    baseline_metrics = regression_metrics(actual, baseline.predict(X_test))
    random_walk_metrics = regression_metrics(actual, np.zeros_like(actual))
    test_scale = _vol_scale(X_test, horizon)
    inside = (actual >= predicted + q_low * test_scale) & (actual <= predicted + q_high * test_scale)
    model_metrics["interval_coverage_80"] = float(np.mean(inside))
    skill = 1 - model_metrics["mae"] / baseline_metrics["mae"] if baseline_metrics["mae"] > 0 else None
    metrics = {
        "test": model_metrics,
        "baseline_historical_mean": baseline_metrics,
        "baseline_zero_return": random_walk_metrics,
        "mae_skill_vs_historical_mean": skill,
        "beats_baseline": bool(skill is not None and skill > 0),
        "validation_mae": best[0],
    }
    backtest = [
        {"date": idx.date().isoformat(), "actual": float(act), "predicted": float(pred),
         "lower": float(pred + q_low * sc), "upper": float(pred + q_high * sc)}
        for idx, act, pred, sc in zip(X_test.index, actual, predicted, test_scale, strict=True)
    ]
    split_dates = {
        "train": [X.index[a].date().isoformat(), X.index[b - 1].date().isoformat()],
        "validation": [X.index[c].date().isoformat(), X.index[d - 1].date().isoformat()],
        "test": [X.index[e].date().isoformat(), X.index[f - 1].date().isoformat()],
        "purge_gap_days": horizon,
    }
    return TrainedForecast(model_type, horizon, final, list(X.columns), params, metrics,
                           (q_low, q_high), split_dates, backtest)


def forecast_latest(trained: TrainedForecast, nav: pd.Series, benchmark: pd.Series | None) -> dict:
    _, _, latest = fx.make_dataset(nav, benchmark, trained.horizon)
    latest = latest[trained.feature_names]
    point = float(trained.model.predict(latest)[0])
    last_nav = float(nav.iloc[-1])
    scale = float(_vol_scale(latest, trained.horizon)[0])
    low, high = point + trained.residual_quantiles[0] * scale, point + trained.residual_quantiles[1] * scale
    return {
        "as_of": latest.index[0].date().isoformat(),
        "last_nav": last_nav,
        "horizon_trading_days": trained.horizon,
        "predicted_log_return": point,
        "predicted_return": math.expm1(point),
        "interval_80": {"lower_return": math.expm1(low), "upper_return": math.expm1(high),
                        "lower_nav": last_nav * math.exp(low), "upper_nav": last_nav * math.exp(high)},
        "predicted_nav": last_nav * math.exp(point),
        "features": {k: float(v) for k, v in latest.iloc[0].items()},
    }


def explain(trained: TrainedForecast, nav: pd.Series, benchmark: pd.Series | None, max_background: int = 200) -> dict:
    """SHAP explanations: local (latest forecast) and global (mean |SHAP| on recent rows)."""
    if trained.model_type == "historical_mean":
        return {"available": False, "reason": "A constant baseline has no feature attributions."}
    import shap

    X, _, latest = fx.make_dataset(nav, benchmark, trained.horizon)  # noqa: N806
    background = X.iloc[-max_background:][trained.feature_names]
    latest = latest[trained.feature_names]
    if trained.model_type == "random_forest":
        explainer = shap.TreeExplainer(trained.model)
        local = explainer.shap_values(latest)[0]
        global_values = explainer.shap_values(background)
        base_value = float(np.ravel(explainer.expected_value)[0])
        method = "TreeExplainer (exact for tree ensembles)"
    else:
        scaler = trained.model.named_steps["scale"]
        ridge = trained.model.named_steps["model"]
        scaled_bg = pd.DataFrame(scaler.transform(background), columns=trained.feature_names)
        explainer = shap.LinearExplainer(ridge, shap.maskers.Independent(scaled_bg, max_samples=len(scaled_bg)))
        local = explainer.shap_values(scaler.transform(latest))[0]
        global_values = explainer.shap_values(scaled_bg.to_numpy())
        base_value = float(np.ravel(explainer.expected_value)[0])
        method = "LinearExplainer (interventional, background = last 200 rows)"
    importance = np.mean(np.abs(global_values), axis=0)
    order = np.argsort(-np.abs(local))
    return {
        "available": True,
        "method": method,
        "base_value": base_value,
        "local": [{"feature": trained.feature_names[i], "label": fx.FEATURE_LABELS.get(trained.feature_names[i]),
                   "value": float(latest.iloc[0, i]), "shap": float(local[i])} for i in order],
        "global_importance": sorted(
            [{"feature": f, "label": fx.FEATURE_LABELS.get(f), "mean_abs_shap": float(v)}
             for f, v in zip(trained.feature_names, importance, strict=True)],
            key=lambda row: -row["mean_abs_shap"]),
        "note": "SHAP values explain this model's output in log-return units; they describe the model, "
                "not causal market effects.",
    }


def save(trained: TrainedForecast, model_dir) -> str:  # type: ignore[no-untyped-def]
    model_dir.mkdir(parents=True, exist_ok=True)
    key = f"{uuid.uuid4().hex}.joblib"
    joblib.dump(trained, model_dir / key)
    return key


def load(model_dir, key: str) -> TrainedForecast:  # type: ignore[no-untyped-def]
    # Artifacts are written only by this server under random names, never
    # uploaded by users, so unpickling them is safe in this deployment.
    if not (len(key) == 39 and key.endswith(".joblib") and all(c in "0123456789abcdef" for c in key[:32])):
        raise ValueError("Invalid model artifact key.")
    return joblib.load(model_dir / key)
