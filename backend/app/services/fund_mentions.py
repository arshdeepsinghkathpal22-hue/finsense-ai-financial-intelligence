"""Finds which funds a free-text question refers to.

Used by the assistant (to choose analytics targets) and by retrieval (so a
question about fund A is not "answered" with a passage about fund B).
Matching is deliberately conservative: scheme codes, full names without the
plan suffix, and short aliases only when they are unique across funds.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fund

_PLAN_SUFFIX = re.compile(r"\s*[-–(].*$")
_GENERIC = {"fund", "funds", "the", "equity", "growth", "direct", "regular", "plan", "india", "large", "mid",
            "small", "cap", "index", "bond", "debt", "liquid", "flexi", "balanced", "advantage", "tax", "saver",
            "opportunities", "dynamic", "fs", "elss", "option", "idcw", "bluechip", "midcap", "smallcap"}


def _aliases(fund: Fund) -> list[str]:
    base = _PLAN_SUFFIX.sub("", fund.name).strip()
    words = base.split()
    aliases = {fund.scheme_code.lower(), base.lower()}
    if words and words[-1].lower() == "fund":
        aliases.add(" ".join(words[:-1]).lower())
    if len(words) >= 2:
        aliases.add(" ".join(words[:2]).lower())
    if words and words[0].lower() not in _GENERIC and len(words[0]) >= 4:
        aliases.add(words[0].lower())
    return [a for a in aliases if a]


def resolve_fund_mentions(db: Session, text: str) -> list[Fund]:
    """Funds named in ``text``. Growth and IDCW variants of one scheme both match."""
    funds = db.scalars(select(Fund)).all()
    alias_map: dict[str, list[Fund]] = {}
    for fund in funds:
        for alias in _aliases(fund):
            alias_map.setdefault(alias, []).append(fund)
    lowered = text.lower()
    found: dict[int, Fund] = {}
    for alias, matches in alias_map.items():
        # A one-word alias shared by unrelated schemes is ambiguous; keep it
        # only when every fund it maps to belongs to the same scheme family.
        families = {_PLAN_SUFFIX.sub("", f.name).strip().lower() for f in matches}
        if len(families) > 1 and " " not in alias:
            continue
        if re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", lowered):
            for fund in matches:
                found[fund.id] = fund
    return sorted(found.values(), key=lambda f: f.id)
