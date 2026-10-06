"""Database-facing ML operations: train-or-reuse, forecast, explain, detect anomalies."""

from __future__ import annotations

import logging
import uuid

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import InsufficientDataError, ValidationFailedError
from app.models import Fund, MlModel
from app.services.analytics import series
from app.services.ml import anomalies, forecasting

logger = logging.getLogger("finsense.ml")


def capabilities() -> dict:
    return {
        "models": [
            {"type": "historical_mean", "label": "Historical mean (baseline)", "available": True},
            {"type": "ridge", "label": "Ridge regression", "available": True},
            {"type": "random_forest", "label": "Random forest", "available": True},
            {"type": "lstm", "label": "LSTM", "available": False,
             "reason": "Not offered: a single fund's daily NAV history is too short to train an LSTM that "
                       "reliably beats the simple baselines, and it would add a large deep-learning dependency."},
        ],
        "horizons_trading_days": list(forecasting.HORIZONS),
        "anomaly_detection": {"method": "Isolation Forest", "available": True},
        "explanations": "SHAP (TreeExplainer for random forest, LinearExplainer for ridge)",
    }


def _load_inputs(db: Session, fund: Fund) -> tuple[pd.Series, pd.Series | None]:
    nav = series.load_fund_nav(db, fund.id)
    benchmark = None
    if fund.benchmark_id:
        try:
            benchmark = series.load_benchmark(db, fund.benchmark_id)
        except InsufficientDataError:
            benchmark = None
    return nav, benchmark


def get_or_train(db: Session, settings: Settings, fund: Fund, *, model_type: str, horizon: int,
                 user_id: uuid.UUID | None) -> tuple[MlModel, forecasting.TrainedForecast, pd.Series, pd.Series | None]:
    if model_type not in forecasting.MODEL_TYPES:
        raise ValidationFailedError(f"model_type must be one of {', '.join(forecasting.MODEL_TYPES)}.")
    if horizon not in forecasting.HORIZONS:
        raise ValidationFailedError(f"horizon must be one of {forecasting.HORIZONS} trading days.")
    nav, benchmark = _load_inputs(db, fund)
    fingerprint = forecasting.data_fingerprint(fund.id, model_type, horizon, nav, benchmark is not None)
    record = db.scalar(select(MlModel).where(
        MlModel.fund_id == fund.id, MlModel.model_type == model_type, MlModel.horizon_days == horizon,
        MlModel.data_fingerprint == fingerprint))
    if record is not None:
        try:
            return record, forecasting.load(settings.model_dir, record.artifact_key), nav, benchmark
        except (FileNotFoundError, ValueError):
            logger.warning("Model artifact %s missing; retraining", record.artifact_key)
            db.delete(record)
            db.commit()
    trained = forecasting.train(nav, benchmark, model_type=model_type, horizon=horizon)
    key = forecasting.save(trained, settings.model_dir)
    record = MlModel(
        fund_id=fund.id, model_type=model_type, horizon_days=horizon, data_fingerprint=fingerprint,
        train_start=pd.Timestamp(trained.split_dates["train"][0]).date(),
        train_end=pd.Timestamp(trained.split_dates["validation"][1]).date(),
        test_end=pd.Timestamp(trained.split_dates["test"][1]).date(),
        feature_names=trained.feature_names, hyperparameters=trained.hyperparameters,
        metrics=trained.metrics | {"splits": trained.split_dates}, artifact_key=key, created_by=user_id,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:  # trained concurrently by another request; use theirs
        db.rollback()
        (settings.model_dir / key).unlink(missing_ok=True)
        record = db.scalar(select(MlModel).where(
            MlModel.fund_id == fund.id, MlModel.model_type == model_type, MlModel.horizon_days == horizon,
            MlModel.data_fingerprint == fingerprint))
        return record, forecasting.load(settings.model_dir, record.artifact_key), nav, benchmark
    return record, trained, nav, benchmark


def forecast(db: Session, settings: Settings, fund: Fund, *, model_type: str, horizon: int,
             user_id: uuid.UUID | None, explain: bool = True) -> dict:
    record, trained, nav, benchmark = get_or_train(db, settings, fund, model_type=model_type, horizon=horizon,
                                                   user_id=user_id)
    latest = forecasting.forecast_latest(trained, nav, benchmark)
    result = {
        "fund": {"id": fund.id, "name": fund.name, "scheme_code": fund.scheme_code, "is_synthetic": fund.is_synthetic},
        "model": {"id": str(record.id), "type": record.model_type, "horizon_trading_days": record.horizon_days,
                  "hyperparameters": record.hyperparameters, "trained_at": record.created_at.isoformat()
                  if record.created_at else None, "features": record.feature_names},
        "evaluation": trained.metrics,
        "splits": trained.split_dates,
        "forecast": latest,
        "backtest": trained.backtest,
        "history": [{"date": d.date().isoformat(), "nav": round(float(v), 4)} for d, v in nav.iloc[-260:].items()],
        "explanation": forecasting.explain(trained, nav, benchmark) if explain else None,
        "disclaimer": (
            "This is a statistical estimate from historical NAV patterns, not a promise of future returns. "
            "The 80% interval is based on past forecast errors; actual outcomes can fall outside it."
        ),
    }
    if not trained.metrics["beats_baseline"]:
        result["warning"] = ("On the held-out test period this model did NOT beat the historical-mean "
                             "baseline, so its forecast should not be relied upon.")
    return result


def detect_anomalies(db: Session, fund: Fund, *, contamination: float) -> dict:
    nav, benchmark = _load_inputs(db, fund)
    result = anomalies.detect(nav, benchmark, contamination=contamination)
    result["fund"] = {"id": fund.id, "name": fund.name, "scheme_code": fund.scheme_code,
                      "is_synthetic": fund.is_synthetic}
    return result
