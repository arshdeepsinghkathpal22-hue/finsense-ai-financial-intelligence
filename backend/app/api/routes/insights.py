"""Dashboard, what-if simulation and ML endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentUser, DbSession, user_rate_limit
from app.core.errors import NotFoundError, ValidationFailedError
from app.models import Fund, MlModel, Portfolio, User
from app.services import simulation
from app.services.analytics import metrics, reports, series
from app.services.ml import service as ml_service
from app.services.portfolio import analysis

router = APIRouter(tags=["insights"])

DEFAULT_DASHBOARD_FUND = "FS-LC-001"


# --------------------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard(
    db: DbSession, settings: AppSettings, user: CurrentUser,
    portfolio_id: uuid.UUID | None = None, fund_id: int | None = None,
    period: Annotated[Literal["1y", "3y", "5y", "max"], Query()] = "3y",
) -> dict:
    portfolios = db.scalars(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.name)).all()
    selection: dict
    if portfolio_id is not None:
        portfolio = next((p for p in portfolios if p.id == portfolio_id), None)
        if portfolio is None:
            raise NotFoundError("Portfolio not found.")
        selection = {"kind": "portfolio", "report": analysis.portfolio_report(db, settings, portfolio, period=period)}
    elif fund_id is not None or not portfolios:
        fund = series.get_fund(db, fund_id) if fund_id is not None else (
            db.scalar(select(Fund).where(Fund.scheme_code == DEFAULT_DASHBOARD_FUND))
            or db.scalar(select(Fund).order_by(Fund.id)))
        if fund is None:
            return {"selection": None, "portfolios": [], "message": "No data loaded yet. Run the seed command."}
        selection = {"kind": "fund", "report": reports.fund_risk_report(db, settings, fund, period=period)}
    else:
        selection = {"kind": "portfolio", "report": analysis.portfolio_report(db, settings, portfolios[0],
                                                                             period=period)}
    return {"selection": selection,
            "portfolios": [{"id": str(p.id), "name": p.name} for p in portfolios]}


# --------------------------------------------------------------------------- simulations


class SipRequest(BaseModel):
    monthly_amount: float = Field(ge=0, le=1e8)
    years: int = Field(ge=1, le=50)
    annual_return: float = Field(gt=-0.5, lt=1.0)
    lumpsum: float = Field(default=0, ge=0, le=1e10)
    annual_step_up: float = Field(default=0, ge=0, le=0.5)


@router.post("/simulate/sip")
def simulate_sip(body: SipRequest, _: CurrentUser) -> dict:
    return simulation.sip_projection(**body.model_dump())


class MonteCarloRequest(SipRequest):
    annual_volatility: float = Field(ge=0, lt=1.5)
    paths: int = Field(default=2000, ge=100, le=20000)
    seed: int = Field(default=7, ge=0, le=2**31 - 1)


@router.post("/simulate/monte-carlo")
def simulate_monte_carlo(body: MonteCarloRequest,
                         _: Annotated[User, Depends(user_rate_limit("simulate"))]) -> dict:
    args = body.model_dump()
    args.pop("annual_step_up")
    return simulation.monte_carlo_sip(**args)


class HoldingsSpec(BaseModel):
    portfolio_id: uuid.UUID | None = None
    fund_ids: list[int] | None = Field(default=None, min_length=1, max_length=20)
    weights_pct: list[float] | None = None
    lookback: Literal["1y", "3y", "5y", "max"] = "3y"

    @model_validator(mode="after")
    def _one_source(self) -> HoldingsSpec:
        if (self.portfolio_id is None) == (self.fund_ids is None):
            raise ValueError("provide either portfolio_id or fund_ids (with weights_pct)")
        if self.fund_ids is not None and (self.weights_pct is None or len(self.weights_pct) != len(self.fund_ids)):
            raise ValueError("weights_pct must have one entry per fund")
        return self


def _resolve_holdings(db, user: User, spec: HoldingsSpec) -> tuple[list[int], list[float], float]:  # type: ignore[no-untyped-def]
    if spec.portfolio_id is not None:
        portfolio = db.get(Portfolio, spec.portfolio_id)
        if portfolio is None or portfolio.user_id != user.id:
            raise NotFoundError("Portfolio not found.")
        assets = sorted(portfolio.assets, key=lambda a: a.fund_id)
        return [a.fund_id for a in assets], [float(a.weight) for a in assets], float(portfolio.initial_value)
    weights = metrics.normalise_weights(spec.weights_pct or []).tolist()
    return list(spec.fund_ids or []), weights, 100000.0


class ShockRequest(HoldingsSpec):
    market_move: float = Field(ge=-0.9, le=0.5)
    portfolio_value: float | None = Field(default=None, gt=0)


@router.post("/simulate/market-shock")
def simulate_market_shock(body: ShockRequest, db: DbSession, settings: AppSettings, user: CurrentUser) -> dict:
    fund_ids, weights, value = _resolve_holdings(db, user, body)
    inputs = analysis.historical_inputs(db, settings, fund_ids, body.lookback)
    result = simulation.market_shock(weights=weights, betas=inputs["betas"], labels=inputs["labels"],
                                     market_decline=body.market_move, portfolio_value=body.portfolio_value or value)
    result["estimation_window"] = inputs["window"]
    result["beta_reference"] = "Each fund's beta against its own benchmark index over the lookback window."
    return result


class StressRequest(HoldingsSpec):
    volatility_multiplier: float = Field(default=1.0, ge=0.1, le=5)
    correlation_override: float | None = Field(default=None, ge=-1, le=1)
    return_shift: float = Field(default=0.0, ge=-0.5, le=0.5)
    horizon_days: int = Field(default=1, ge=1, le=252)
    confidence: float = Field(default=0.95, ge=0.9, le=0.995)


@router.post("/simulate/stress")
def simulate_stress(body: StressRequest, db: DbSession, settings: AppSettings, user: CurrentUser) -> dict:
    fund_ids, weights, _ = _resolve_holdings(db, user, body)
    inputs = analysis.historical_inputs(db, settings, fund_ids, body.lookback)
    result = simulation.assumption_stress(
        weights=weights, expected_returns=inputs["expected_returns"], volatilities=inputs["volatilities"],
        correlation=inputs["correlation"], volatility_multiplier=body.volatility_multiplier,
        correlation_override=body.correlation_override, return_shift=body.return_shift,
        confidence=body.confidence, risk_free_rate=settings.risk_free_rate, horizon_days=body.horizon_days,
        periods_per_year=settings.trading_days_per_year,
    )
    result["labels"] = inputs["labels"]
    result["estimation_window"] = inputs["window"]
    return result


class AllocationChangeRequest(BaseModel):
    fund_ids: list[int] = Field(min_length=1, max_length=20)
    current_weights_pct: list[float]
    proposed_weights_pct: list[float]
    lookback: Literal["1y", "3y", "5y", "max"] = "3y"

    @model_validator(mode="after")
    def _lengths(self) -> AllocationChangeRequest:
        n = len(self.fund_ids)
        if len(self.current_weights_pct) != n or len(self.proposed_weights_pct) != n:
            raise ValueError("provide one current and one proposed weight per fund")
        return self


@router.post("/simulate/allocation-change")
def simulate_allocation_change(body: AllocationChangeRequest, db: DbSession, settings: AppSettings,
                               _: CurrentUser) -> dict:
    if len(set(body.fund_ids)) != len(body.fund_ids):
        raise ValidationFailedError("Each fund may appear only once.")
    inputs = analysis.historical_inputs(db, settings, body.fund_ids, body.lookback)
    out = {}
    for label, weights in (("current", body.current_weights_pct), ("proposed", body.proposed_weights_pct)):
        out[label] = simulation.assumption_stress(
            weights=weights, expected_returns=inputs["expected_returns"], volatilities=inputs["volatilities"],
            correlation=inputs["correlation"], risk_free_rate=settings.risk_free_rate,
            periods_per_year=settings.trading_days_per_year)["baseline"] | {
            "weights": metrics.normalise_weights(weights).tolist()}
    return {"labels": inputs["labels"], **out, "estimation_window": inputs["window"],
            "assumptions": {"method": "Historical mean returns and covariance over the lookback window; "
                                      "parametric (normal) 1-day VaR."}}


# --------------------------------------------------------------------------- ML


@router.get("/ml/capabilities")
def ml_capabilities(_: CurrentUser) -> dict:
    return ml_service.capabilities()


class ForecastRequest(BaseModel):
    fund_id: int
    horizon_days: Literal[5, 21, 63] = 21
    model_type: Literal["ridge", "random_forest", "historical_mean"] = "random_forest"
    explain: bool = True


@router.post("/ml/forecast")
def ml_forecast(body: ForecastRequest, db: DbSession, settings: AppSettings,
                user: Annotated[User, Depends(user_rate_limit("ml"))]) -> dict:
    fund = series.get_fund(db, body.fund_id)
    return ml_service.forecast(db, settings, fund, model_type=body.model_type, horizon=body.horizon_days,
                               user_id=user.id, explain=body.explain)


class AnomalyRequest(BaseModel):
    fund_id: int
    contamination: float = Field(default=0.01, ge=0.001, le=0.1)


@router.post("/ml/anomalies")
def ml_anomalies(body: AnomalyRequest, db: DbSession,
                 _: Annotated[User, Depends(user_rate_limit("ml"))]) -> dict:
    fund = series.get_fund(db, body.fund_id)
    return ml_service.detect_anomalies(db, fund, contamination=body.contamination)


@router.get("/ml/models")
def ml_models(db: DbSession, _: CurrentUser, fund_id: int | None = None) -> dict:
    query = select(MlModel).order_by(MlModel.created_at.desc()).limit(100)
    if fund_id is not None:
        query = query.where(MlModel.fund_id == fund_id)
    rows = db.scalars(query).all()
    return {"items": [{
        "id": str(m.id), "fund_id": m.fund_id, "model_type": m.model_type, "horizon_days": m.horizon_days,
        "train_start": m.train_start.isoformat(), "train_end": m.train_end.isoformat(),
        "test_end": m.test_end.isoformat(), "hyperparameters": m.hyperparameters,
        "test_mae": m.metrics.get("test", {}).get("mae"),
        "beats_baseline": m.metrics.get("beats_baseline"), "created_at": m.created_at.isoformat(),
    } for m in rows]}
