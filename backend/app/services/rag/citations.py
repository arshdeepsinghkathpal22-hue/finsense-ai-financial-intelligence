"""Citation validation and numeric grounding checks for generated answers.

The model is only ever shown opaque markers ([S1], [T2]); filenames, page
numbers and document ids are attached by the server from the evidence it
actually supplied. Any marker the model invents is removed and reported,
so a citation can never point at a document that was not retrieved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MARKER = re.compile(r"\[([ST])(\d{1,3})\]")
_GROUPED = re.compile(r"\[((?:[ST]\d{1,3}\s*[,;]\s*)+[ST]\d{1,3})\]")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?%?")


@dataclass
class CitationCheck:
    text: str
    used_sources: list[str] = field(default_factory=list)
    used_calculations: list[str] = field(default_factory=list)
    invalid_markers: list[str] = field(default_factory=list)

    @property
    def all_valid(self) -> bool:
        return not self.invalid_markers


def _expand_grouped(text: str) -> str:
    """Rewrites "[S1, S2]" as "[S1][S2]"."""
    return _GROUPED.sub(lambda m: "".join(f"[{p.strip()}]" for p in re.split(r"[,;]", m.group(1))), text)


def validate_citations(text: str, valid_sources: set[str], valid_calculations: set[str]) -> CitationCheck:
    text = _expand_grouped(text)
    check = CitationCheck(text="")
    seen_s: list[str] = []
    seen_t: list[str] = []

    def replace(match: re.Match[str]) -> str:
        marker = f"{match.group(1)}{match.group(2)}"
        valid = valid_sources if match.group(1) == "S" else valid_calculations
        if marker not in valid:
            check.invalid_markers.append(marker)
            return ""
        target = seen_s if match.group(1) == "S" else seen_t
        if marker not in target:
            target.append(marker)
        return match.group(0)

    cleaned = MARKER.sub(replace, text)
    check.text = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    check.used_sources = seen_s
    check.used_calculations = seen_t
    return check


def _numbers(text: str) -> list[tuple[str, float, int]]:
    out = []
    for raw in _NUMBER.findall(MARKER.sub(" ", text)):
        clean = raw.replace(",", "").rstrip("%")
        try:
            value = float(clean)
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        out.append((raw, value, decimals))
    return out


def ungrounded_numbers(answer: str, sources: list[str], question: str = "") -> list[str]:
    """Numbers in the answer that appear in none of the evidence or calculations.

    A number counts as grounded if some source number rounds to it at the
    precision the answer used (so "14.7%" is grounded by "14.65%"). Small
    integers and four-digit years are ignored: they are usually ordinal or
    date references rather than figures.
    """
    pool = []
    for text in [*sources, question]:
        pool += [value for _, value, _ in _numbers(text)]
    missing = []
    for raw, value, decimals in _numbers(answer):
        if decimals == 0 and (abs(value) <= 12 or 1900 <= value <= 2100):
            continue
        tolerance = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(value - p) <= tolerance for p in pool):
            missing.append(raw)
    return sorted(set(missing))
