"""Optional live-data adapter for AMFI's public daily NAV file.

The Association of Mutual Funds in India publishes the latest NAV of every
scheme at https://www.amfiindia.com/spages/NAVAll.txt (semicolon-separated:
``Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme
Name;Net Asset Value;Date``, interleaved with category and AMC header
lines). The adapter:

* is disabled unless ``AMFI_ENABLED=true``;
* only updates funds an administrator registered from AMFI (scheme codes
  in the funds table whose source is ``amfi``) - it never touches the
  synthetic demo funds;
* uses a timeout, three attempts with back-off and an on-disk cache;
* records the observation date AMFI reports, so data freshness is shown
  truthfully (the latest NAV is usually the previous business day).

Review AMFI's website terms before using the data beyond personal or
educational use.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import ServiceUnavailableError, ValidationFailedError
from app.models import Fund, NavObservation
from app.services.ingestion.csv_import import ensure_default_sources, get_source

logger = logging.getLogger("finsense.amfi")
CACHE_FILE = "amfi_navall.txt"


def parse_navall(text: str) -> dict[str, dict]:
    """Parses NAVAll.txt into {scheme_code: {name, nav, date}}; malformed lines are skipped."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) != 6 or not parts[0].isdigit():
            continue
        code, _, _, name, nav_text, date_text = parts
        try:
            nav = Decimal(nav_text)
            observed = datetime.strptime(date_text, "%d-%b-%Y").date()
        except (InvalidOperation, ValueError):
            continue
        if nav <= 0:
            continue
        out[code] = {"name": name, "nav": nav, "date": observed}
    return out


def fetch_navall(settings: Settings, transport: httpx.BaseTransport | None = None) -> str:
    if not settings.amfi_enabled:
        raise ServiceUnavailableError("AMFI live data is disabled (set AMFI_ENABLED=true to enable).")
    cache = settings.cache_dir / CACHE_FILE
    if cache.exists():
        age = datetime.now(UTC) - datetime.fromtimestamp(cache.stat().st_mtime, UTC)
        if age < timedelta(minutes=settings.amfi_cache_minutes):
            return cache.read_text(encoding="utf-8")
    last_error: Exception | None = None
    with httpx.Client(timeout=settings.amfi_timeout_s, transport=transport, follow_redirects=False) as client:
        for attempt in range(3):
            try:
                response = client.get(settings.amfi_nav_url)
                if response.status_code == 200 and len(response.content) < 50 * 1024 * 1024:
                    text = response.text
                    settings.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache.write_text(text, encoding="utf-8")
                    return text
                last_error = ServiceUnavailableError(f"AMFI returned HTTP {response.status_code}.")
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(min(2**attempt, 4) if transport is None else 0)
    logger.warning("AMFI fetch failed: %s", type(last_error).__name__)
    raise ServiceUnavailableError("The AMFI NAV file could not be downloaded; try again later.")


def register_scheme(db: Session, settings: Settings, scheme_code: str, *, category: str, asset_class: str,
                    transport: httpx.BaseTransport | None = None) -> Fund:
    """Adds a real AMFI scheme to track (its NAV history accrues from today, or via CSV import)."""
    data = parse_navall(fetch_navall(settings, transport))
    if scheme_code not in data:
        raise ValidationFailedError(f"Scheme code {scheme_code} was not found in the AMFI NAV file.")
    ensure_default_sources(db)
    source = get_source(db, "amfi")
    if db.scalar(select(Fund.id).where(Fund.scheme_code == scheme_code)) is not None:
        raise ValidationFailedError("That scheme code is already registered.")
    name = data[scheme_code]["name"]
    option = "IDCW" if any(k in name.upper() for k in ("IDCW", "DIVIDEND")) else "Growth"
    fund = Fund(scheme_code=scheme_code, name=name[:200], category=category, asset_class=asset_class,
                option=option, return_basis="nav_price" if option == "IDCW" else "nav_growth",
                source_id=source.id, is_synthetic=False)
    db.add(fund)
    db.commit()
    return fund


def sync_latest_navs(db: Session, settings: Settings, transport: httpx.BaseTransport | None = None) -> dict:
    data = parse_navall(fetch_navall(settings, transport))
    source = get_source(db, "amfi")
    funds = db.scalars(select(Fund).where(Fund.source_id == source.id)).all()
    updated, missing = [], []
    for fund in funds:
        row = data.get(fund.scheme_code)
        if row is None:
            missing.append(fund.scheme_code)
            continue
        stmt = insert(NavObservation).values(fund_id=fund.id, obs_date=row["date"], nav=row["nav"],
                                             source_id=source.id)
        db.execute(stmt.on_conflict_do_update(index_elements=["fund_id", "obs_date"],
                                              set_={"nav": stmt.excluded.nav}))
        updated.append({"scheme_code": fund.scheme_code, "date": row["date"].isoformat(), "nav": str(row["nav"])})
    db.commit()
    return {"tracked": len(funds), "updated": updated, "missing": missing,
            "fetched_at": datetime.now(UTC).isoformat(), "today": date.today().isoformat()}
