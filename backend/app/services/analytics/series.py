"""Loading time series from the database and resolving analysis periods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import InsufficientDataError, NotFoundError, ValidationFailedError
from app.models import Benchmark, BenchmarkObservation, Fund, NavObservation

PERIOD_YEARS = {"1m": 1 / 12, "3m": 0.25, "6m": 0.5, "1y": 1, "3y": 3, "5y": 5}
VALID_PERIODS = (*PERIOD_YEARS.keys(), "max")


@dataclass(frozen=True)
class Freshness:
    latest_observation: date | None
    age_days: int | None
    is_stale: bool

    def as_dict(self) -> dict:
        return {
            "latest_observation": self.latest_observation.isoformat()
            if self.latest_observation
            else None,
            "age_days": self.age_days,
            "is_stale": self.is_stale,
        }


def freshness(latest: date | None, stale_after_days: int, today: date | None = None) -> Freshness:
    if latest is None:
        return Freshness(None, None, True)
    today = today or datetime.now(UTC).date()
    age = (today - latest).days
    return Freshness(latest, age, age > stale_after_days)


def get_fund(db: Session, fund_id: int) -> Fund:
    fund = db.get(Fund, fund_id)
    if fund is None:
        raise NotFoundError("Fund not found.")
    return fund


def latest_nav_date(db: Session, fund_id: int) -> date | None:
    return db.scalar(select(func.max(NavObservation.obs_date)).where(NavObservation.fund_id == fund_id))


def resolve_period(
    period: str, latest: date, earliest: date, start: date | None = None, end: date | None = None
) -> tuple[date, date]:
    """Turns a period label (or explicit dates) into a [start, end] window.

    Periods are measured back from the latest available observation, not
    from today, so stale data is never silently mixed with "now".
    """
    if start or end:
        window_end = end or latest
        window_start = start or earliest
    elif period == "max":
        window_start, window_end = earliest, latest
    elif period in PERIOD_YEARS:
        months = round(PERIOD_YEARS[period] * 12)
        window_end = latest
        window_start = (pd.Timestamp(latest) - pd.DateOffset(months=months)).date()
    else:
        raise ValidationFailedError(f"Unknown period '{period}'. Use one of {', '.join(VALID_PERIODS)}.")
    if window_start >= window_end:
        raise ValidationFailedError("The start date must be before the end date.")
    return max(window_start, earliest), window_end


def history_covers(period: str, latest: date, earliest: date, tolerance_days: int = 5) -> bool:
    """True when the available history reaches back to the start of a period label.

    ``resolve_period`` clamps a window to the first observation, so a "5y"
    request on a three-year-old fund silently becomes "since launch"; callers
    use this check to label such windows honestly instead.
    """
    if period not in PERIOD_YEARS:
        return True
    months = round(PERIOD_YEARS[period] * 12)
    requested = (pd.Timestamp(latest) - pd.DateOffset(months=months)).date()
    return (requested - earliest).days >= -tolerance_days


def load_fund_nav(db: Session, fund_id: int, start: date | None = None, end: date | None = None) -> pd.Series:
    query = select(NavObservation.obs_date, NavObservation.nav).where(NavObservation.fund_id == fund_id)
    if start:
        query = query.where(NavObservation.obs_date >= start)
    if end:
        query = query.where(NavObservation.obs_date <= end)
    rows = db.execute(query.order_by(NavObservation.obs_date)).all()
    if not rows:
        raise InsufficientDataError("No NAV observations are available for this fund and period.")
    index = pd.DatetimeIndex([row[0] for row in rows], name="date")
    return pd.Series([float(row[1]) for row in rows], index=index, name="nav")


def load_benchmark(
    db: Session, benchmark_id: int, start: date | None = None, end: date | None = None
) -> pd.Series:
    query = select(BenchmarkObservation.obs_date, BenchmarkObservation.value).where(
        BenchmarkObservation.benchmark_id == benchmark_id
    )
    if start:
        query = query.where(BenchmarkObservation.obs_date >= start)
    if end:
        query = query.where(BenchmarkObservation.obs_date <= end)
    rows = db.execute(query.order_by(BenchmarkObservation.obs_date)).all()
    if not rows:
        raise InsufficientDataError("No benchmark observations are available for this period.")
    index = pd.DatetimeIndex([row[0] for row in rows], name="date")
    return pd.Series([float(row[1]) for row in rows], index=index, name="benchmark")


def load_nav_frame(
    db: Session, fund_ids: list[int], start: date | None = None, end: date | None = None
) -> tuple[pd.DataFrame, dict]:
    """NAVs for several funds aligned on common dates.

    Returns the aligned frame plus a report of how many dates were dropped
    because at least one fund had no observation on them.
    """
    columns = {fund_id: load_fund_nav(db, fund_id, start, end) for fund_id in fund_ids}
    union = pd.concat(columns, axis=1, join="outer")
    aligned = union.dropna()
    report = {
        "dates_in_union": int(len(union)),
        "dates_aligned": int(len(aligned)),
        "dates_dropped": int(len(union) - len(aligned)),
    }
    if len(aligned) < 2:
        raise InsufficientDataError(
            "The selected funds have too few overlapping observations to analyse together.",
            report,
        )
    return aligned, report


def get_benchmark(db: Session, benchmark_id: int) -> Benchmark:
    benchmark = db.get(Benchmark, benchmark_id)
    if benchmark is None:
        raise NotFoundError("Benchmark not found.")
    return benchmark
