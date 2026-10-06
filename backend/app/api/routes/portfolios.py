"""Portfolio CRUD, analytics, optimisation and export. Every route checks ownership."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AppSettings, CurrentUser, DbSession, user_rate_limit
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.models import Fund, Portfolio, PortfolioAsset, User
from app.services import audit
from app.services.analytics import series
from app.services.ingestion.validation import parse_code, parse_percent
from app.services.portfolio import analysis, optimizer

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

WEIGHT_TOLERANCE_PCT = Decimal("0.01")


class AssetIn(BaseModel):
    fund_id: int
    weight_pct: float = Field(gt=0, le=100)


def validate_assets(assets: list[AssetIn]) -> list[AssetIn]:
    ids = [a.fund_id for a in assets]
    if len(ids) != len(set(ids)):
        raise ValueError("each fund may appear only once")
    total = sum(Decimal(str(a.weight_pct)) for a in assets)
    if abs(total - 100) > WEIGHT_TOLERANCE_PCT:
        raise ValueError(f"weights must sum to 100% (got {total}%)")
    return assets


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    initial_value: float = Field(gt=0, le=1e12)
    start_date: date | None = None
    benchmark_id: int | None = None
    assets: list[AssetIn] = Field(min_length=1, max_length=20)

    _assets_valid = field_validator("assets")(validate_assets)


class OptimiseRequest(BaseModel):
    objective: Literal["mean_variance", "min_variance", "max_sharpe"] = "mean_variance"
    risk_profile: Literal["conservative", "moderate", "aggressive"] | None = "moderate"
    risk_aversion: float | None = Field(default=None, gt=0, le=100)
    min_weight: float = Field(default=0.0, ge=0, le=1)
    max_weight: float = Field(default=1.0, gt=0, le=1)
    lookback: Literal["1y", "3y", "5y", "max"] = "3y"
    risk_free_rate: float | None = Field(default=None, ge=0, le=0.2)


class AdHocOptimiseRequest(OptimiseRequest):
    fund_ids: list[int] = Field(min_length=2, max_length=20)
    current_weights_pct: list[float] | None = None


class WeightsIn(BaseModel):
    assets: list[AssetIn] = Field(min_length=1, max_length=20)

    _assets_valid = field_validator("assets")(validate_assets)


def _owned(db: Session, user: User, portfolio_id: uuid.UUID) -> Portfolio:
    portfolio = db.get(Portfolio, portfolio_id)
    # 404 (not 403) so other users' portfolio ids cannot be probed.
    if portfolio is None or portfolio.user_id != user.id:
        raise NotFoundError("Portfolio not found.")
    return portfolio


def _out(portfolio: Portfolio) -> dict:
    return {
        "id": str(portfolio.id), "name": portfolio.name, "description": portfolio.description,
        "initial_value": float(portfolio.initial_value),
        "start_date": portfolio.start_date.isoformat() if portfolio.start_date else None,
        "benchmark_id": portfolio.benchmark_id,
        "created_at": portfolio.created_at.isoformat(), "updated_at": portfolio.updated_at.isoformat(),
        "assets": [{"fund_id": a.fund_id, "scheme_code": a.fund.scheme_code, "name": a.fund.name,
                    "is_synthetic": a.fund.is_synthetic, "weight_pct": float(a.weight) * 100}
                   for a in sorted(portfolio.assets, key=lambda a: -a.weight)],
    }


def _to_fraction(weight_pct: float) -> Decimal:
    return (Decimal(str(weight_pct)) / 100).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


def _set_assets(db: Session, portfolio: Portfolio, assets: list[AssetIn]) -> None:
    for asset in assets:
        series.get_fund(db, asset.fund_id)  # validates the identifier
    portfolio.assets.clear()
    db.flush()
    for asset in assets:
        portfolio.assets.append(PortfolioAsset(fund_id=asset.fund_id, weight=_to_fraction(asset.weight_pct)))


def _ensure_name_available(db: Session, user: User, name: str, exclude: uuid.UUID | None = None) -> None:
    query = select(Portfolio.id).where(Portfolio.user_id == user.id, func.lower(Portfolio.name) == name.lower())
    if exclude is not None:
        query = query.where(Portfolio.id != exclude)
    if db.scalar(query) is not None:
        raise ConflictError("You already have a portfolio with that name.")


def _save_with_assets(db: Session, portfolio: Portfolio, assets: list[AssetIn]) -> None:
    """Writes allocations and commits; a concurrent duplicate name becomes a 409, not a 500."""
    try:
        _set_assets(db, portfolio, assets)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("You already have a portfolio with that name.") from exc


@router.get("")
def list_portfolios(db: DbSession, user: CurrentUser) -> dict:
    rows = db.scalars(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.name)).all()
    return {"items": [_out(p) for p in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_portfolio(body: PortfolioIn, db: DbSession, user: CurrentUser) -> dict:
    if body.benchmark_id is not None:
        series.get_benchmark(db, body.benchmark_id)
    _ensure_name_available(db, user, body.name.strip())
    portfolio = Portfolio(user_id=user.id, name=body.name.strip(), description=body.description,
                          initial_value=Decimal(str(body.initial_value)), start_date=body.start_date,
                          benchmark_id=body.benchmark_id)
    db.add(portfolio)
    _save_with_assets(db, portfolio, body.assets)
    return _out(portfolio)


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_portfolio(
    db: DbSession, user: CurrentUser, file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1, max_length=100)],
    initial_value: Annotated[float, Form(gt=0, le=1e12)] = 100000.0,
) -> dict:
    """Creates a portfolio from a CSV with columns scheme_code, weight_pct."""
    content = await file.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise ValidationFailedError("Portfolio CSV files are limited to 256 KB.")
    try:
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValidationFailedError("The file is not a valid UTF-8 CSV.") from exc
    assets, problems = [], []
    for line, row in enumerate(rows, start=2):
        try:
            code = parse_code(row.get("scheme_code") or "")
            weight = float(parse_percent(row.get("weight_pct") or ""))
        except ValueError as exc:
            problems.append(f"line {line}: {exc}")
            continue
        fund = db.scalar(select(Fund).where(Fund.scheme_code == code))
        if fund is None:
            problems.append(f"line {line}: unknown scheme_code '{code}'")
            continue
        assets.append(AssetIn(fund_id=fund.id, weight_pct=weight))
    if problems:
        raise ValidationFailedError("The portfolio file has problems.", {"problems": problems[:50]})
    body = PortfolioIn(name=name, initial_value=initial_value, assets=assets)
    return create_portfolio(body, db, user)


@router.post("/optimize")
def optimise_ad_hoc(
    body: AdHocOptimiseRequest, db: DbSession, settings: AppSettings,
    user: Annotated[User, Depends(user_rate_limit("optimise"))],
) -> dict:
    """Optimises an unsaved selection of funds (used by the portfolio builder)."""
    current = None
    if body.current_weights_pct is not None:
        if len(body.current_weights_pct) != len(body.fund_ids):
            raise ValidationFailedError("Provide one current weight per fund.")
        current = [w / 100 for w in body.current_weights_pct]
    return _run_optimiser(db, settings, user, body, body.fund_ids, current)


def _run_optimiser(db, settings, user, body: OptimiseRequest, fund_ids, current):  # type: ignore[no-untyped-def]
    risk_aversion = body.risk_aversion or optimizer.RISK_PROFILES[body.risk_profile or "moderate"]
    rf = body.risk_free_rate if body.risk_free_rate is not None else (user.preferences or {}).get("risk_free_rate")
    return analysis.optimise(
        db, settings, fund_ids=fund_ids, current_weights=current, objective=body.objective,
        risk_aversion=risk_aversion, min_weight=body.min_weight, max_weight=body.max_weight,
        lookback=body.lookback, risk_free_rate=rf,
    )


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: uuid.UUID, db: DbSession, user: CurrentUser) -> dict:
    return _out(_owned(db, user, portfolio_id))


@router.put("/{portfolio_id}")
def update_portfolio(portfolio_id: uuid.UUID, body: PortfolioIn, db: DbSession, user: CurrentUser) -> dict:
    portfolio = _owned(db, user, portfolio_id)
    if body.benchmark_id is not None:
        series.get_benchmark(db, body.benchmark_id)
    _ensure_name_available(db, user, body.name.strip(), exclude=portfolio.id)
    portfolio.name = body.name.strip()
    portfolio.description = body.description
    portfolio.initial_value = Decimal(str(body.initial_value))
    portfolio.start_date = body.start_date
    portfolio.benchmark_id = body.benchmark_id
    _save_with_assets(db, portfolio, body.assets)
    return _out(portfolio)


@router.put("/{portfolio_id}/weights")
def apply_weights(portfolio_id: uuid.UUID, body: WeightsIn, db: DbSession, user: CurrentUser) -> dict:
    """Replaces allocations (e.g. to apply an optimised allocation), keeping other settings."""
    portfolio = _owned(db, user, portfolio_id)
    _set_assets(db, portfolio, body.assets)
    db.commit()
    return _out(portfolio)


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(portfolio_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    portfolio = _owned(db, user, portfolio_id)
    db.delete(portfolio)
    audit.record(db, "portfolio.deleted", user_id=user.id, details={"portfolio_id": str(portfolio_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{portfolio_id}/analytics")
def portfolio_analytics(
    portfolio_id: uuid.UUID, db: DbSession, settings: AppSettings, user: CurrentUser,
    period: Annotated[Literal["1y", "3y", "5y", "max"], Query()] = "3y",
    confidence: Annotated[float, Query(ge=0.9, le=0.995)] = 0.95,
) -> dict:
    portfolio = _owned(db, user, portfolio_id)
    rf = (user.preferences or {}).get("risk_free_rate")
    return analysis.portfolio_report(db, settings, portfolio, period=period, risk_free_rate=rf,
                                     confidence=confidence)


@router.post("/{portfolio_id}/optimize")
def optimise_portfolio(
    portfolio_id: uuid.UUID, body: OptimiseRequest, db: DbSession, settings: AppSettings,
    user: Annotated[User, Depends(user_rate_limit("optimise"))],
) -> dict:
    portfolio = _owned(db, user, portfolio_id)
    assets = sorted(portfolio.assets, key=lambda a: a.fund_id)
    result = _run_optimiser(db, settings, user, body, [a.fund_id for a in assets], [float(a.weight) for a in assets])
    result["portfolio_id"] = str(portfolio.id)
    return result


@router.get("/{portfolio_id}/export.csv")
def export_portfolio(portfolio_id: uuid.UUID, db: DbSession, settings: AppSettings, user: CurrentUser) -> Response:
    portfolio = _owned(db, user, portfolio_id)
    report = analysis.portfolio_report(db, settings, portfolio, period="max")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["scheme_code", "fund_name", "initial_weight_pct", "current_weight_pct", "current_value_inr",
                     "risk_contribution_pct", "synthetic_data"])
    for h in report["allocation"]["holdings"]:
        writer.writerow([h["scheme_code"], h["name"], f"{h['initial_weight'] * 100:.2f}",
                         f"{h['current_weight'] * 100:.2f}", f"{h['current_value']:.2f}",
                         "" if h["risk_contribution"] is None else f"{h['risk_contribution'] * 100:.2f}",
                         "yes" if h["is_synthetic"] else "no"])
    writer.writerow([])
    writer.writerow([f"# As of {report['value']['as_of']}; buy-and-hold from {report['period']['start']}. "
                     "Historical analysis only; not investment advice."])
    filename = "".join(c if c.isalnum() or c in "-_" else "_" for c in portfolio.name)[:60] or "portfolio"
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
