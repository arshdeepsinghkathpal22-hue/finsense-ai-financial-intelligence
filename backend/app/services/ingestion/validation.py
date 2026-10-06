"""Parsing and validation of financial CSV files (no database access).

The parser never "repairs" data silently: every rejected row is recorded
with its line number and a reason, and suspicious-but-plausible values are
kept and reported as warnings instead of being dropped.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import PayloadTooLargeError, ValidationFailedError

MAX_ROWS = 500_000
MAX_REJECTED_ROWS_STORED = 500

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y")
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]{0,39}$")


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    parser: Callable[[str], Any]
    required: bool = True


@dataclass(frozen=True)
class KindSpec:
    kind: str
    columns: tuple[ColumnSpec, ...]
    key: tuple[str, ...]

    @property
    def required_columns(self) -> set[str]:
        return {c.name for c in self.columns if c.required}


@dataclass
class ParsedFile:
    kind: str
    records: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_total: int = 0

    def reject(self, line: int, reason: str, raw: dict | None = None) -> None:
        self.rejected.append({"line": line, "reason": reason, "values": raw or {}})


# --------------------------------------------------------------------------- field parsers


def parse_date(value: str) -> date:
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a valid date (expected YYYY-MM-DD or DD-MM-YYYY)")


def parse_decimal(value: str) -> Decimal:
    # Accept common Indian formatting: "Rs 1,234.50", "₹ 1,234.50".
    cleaned = value.strip().replace(",", "").replace("₹", "").replace("Rs", "").strip()
    if not cleaned:
        raise ValueError("value is missing")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' is not a number") from exc
    if not number.is_finite():
        raise ValueError(f"'{value}' is not a finite number")
    return number


def parse_positive(value: str) -> Decimal:
    number = parse_decimal(value)
    if number <= 0:
        raise ValueError(f"value must be positive, got {number}")
    return number


def parse_non_negative(value: str) -> Decimal:
    number = parse_decimal(value)
    if number < 0:
        raise ValueError(f"value must not be negative, got {number}")
    return number


def parse_percent(value: str) -> Decimal:
    number = parse_decimal(value.replace("%", ""))
    if not Decimal(0) <= number <= Decimal(100):
        raise ValueError(f"percentage must be between 0 and 100, got {number}")
    return number


def parse_code(value: str) -> str:
    code = value.strip()
    if not _CODE_RE.match(code):
        raise ValueError(f"'{value}' is not a valid code (letters, digits, - _ . / up to 40 chars)")
    return code


def parse_text(max_len: int) -> Callable[[str], str]:
    def parser(value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("value is missing")
        if len(text) > max_len:
            raise ValueError(f"text longer than {max_len} characters")
        return text

    return parser


def parse_choice(*choices: str) -> Callable[[str], str]:
    def parser(value: str) -> str:
        text = value.strip().lower()
        if text not in choices:
            raise ValueError(f"must be one of {', '.join(choices)}")
        return text

    return parser


def parse_int(value: str) -> int:
    number = parse_non_negative(value)
    if number != number.to_integral_value():
        raise ValueError("must be a whole number")
    return int(number)


def parse_month(value: str) -> date:
    return parse_date(value).replace(day=1)


SPECS: dict[str, KindSpec] = {
    "benchmarks": KindSpec("benchmarks", (
        ColumnSpec("benchmark_code", parse_code),
        ColumnSpec("name", parse_text(160)),
        ColumnSpec("return_basis", parse_choice("price", "total_return")),
    ), ("benchmark_code",)),
    "funds": KindSpec("funds", (
        ColumnSpec("scheme_code", parse_code),
        ColumnSpec("name", parse_text(200)),
        ColumnSpec("category", parse_text(80)),
        ColumnSpec("asset_class", parse_choice("equity", "debt", "hybrid", "other")),
        ColumnSpec("amc", parse_text(120), required=False),
        ColumnSpec("plan", parse_text(16), required=False),
        ColumnSpec("option", parse_text(16), required=False),
        ColumnSpec("benchmark_code", parse_code, required=False),
        ColumnSpec("launch_date", parse_date, required=False),
        ColumnSpec("expense_ratio_pct", parse_percent, required=False),
        ColumnSpec("risk_label", parse_text(40), required=False),
    ), ("scheme_code",)),
    "nav": KindSpec("nav", (
        ColumnSpec("scheme_code", parse_code),
        ColumnSpec("date", parse_date),
        ColumnSpec("nav", parse_positive),
    ), ("scheme_code", "date")),
    "benchmark_values": KindSpec("benchmark_values", (
        ColumnSpec("benchmark_code", parse_code),
        ColumnSpec("date", parse_date),
        ColumnSpec("value", parse_positive),
    ), ("benchmark_code", "date")),
    "aum": KindSpec("aum", (
        ColumnSpec("scheme_code", parse_code),
        ColumnSpec("as_of_date", parse_date),
        ColumnSpec("aum_crore", parse_non_negative),
    ), ("scheme_code", "as_of_date")),
    "sip_flows": KindSpec("sip_flows", (
        ColumnSpec("scheme_code", parse_code),
        ColumnSpec("month", parse_month),
        ColumnSpec("sip_inflow_crore", parse_non_negative),
        ColumnSpec("sip_accounts", parse_int, required=False),
    ), ("scheme_code", "month")),
    "holdings": KindSpec("holdings", (
        ColumnSpec("scheme_code", parse_code),
        ColumnSpec("as_of_date", parse_date),
        ColumnSpec("holding_name", parse_text(160)),
        ColumnSpec("sector", parse_text(80)),
        ColumnSpec("asset_type", parse_text(40)),
        ColumnSpec("weight_pct", parse_percent),
    ), ("scheme_code", "as_of_date", "holding_name")),
}


# --------------------------------------------------------------------------- file level


def decode_csv_bytes(content: bytes, *, max_bytes: int) -> str:
    if len(content) > max_bytes:
        raise PayloadTooLargeError(f"CSV file exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    if not content.strip():
        raise ValidationFailedError("The CSV file is empty.")
    if b"\x00" in content[:8192]:
        raise ValidationFailedError("The file appears to be binary, not CSV text.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailedError("CSV files must be UTF-8 encoded.") from exc


def _normalise_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def parse_csv(kind: str, text: str, *, today: date | None = None) -> ParsedFile:
    if kind not in SPECS:
        raise ValidationFailedError(f"Unknown import type '{kind}'.", {"supported": sorted(SPECS)})
    spec = SPECS[kind]
    today = today or datetime.now(UTC).date()
    reader = csv.reader(io.StringIO(text))
    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise ValidationFailedError("The CSV file has no header row.") from exc
    except csv.Error as exc:
        raise ValidationFailedError(f"The CSV header could not be read: {exc}") from exc
    header = [_normalise_header(h) for h in raw_header]
    missing = spec.required_columns - set(header)
    if missing:
        raise ValidationFailedError(
            f"Missing required column(s): {', '.join(sorted(missing))}.",
            {"expected": [c.name for c in spec.columns], "found": header},
        )
    if len(set(header)) != len(header):
        raise ValidationFailedError("The header contains duplicate column names.")

    result = ParsedFile(kind=kind)
    known = {c.name for c in spec.columns}
    unknown = [h for h in header if h not in known]
    if unknown:
        result.warnings.append(f"Ignored unknown column(s): {', '.join(unknown)}.")

    seen: dict[tuple, tuple[int, dict]] = {}
    conflicting: set[tuple] = set()
    line = 1
    try:
        for row in reader:
            line += 1
            if not any(cell.strip() for cell in row):
                continue
            result.rows_total += 1
            if result.rows_total > MAX_ROWS:
                raise ValidationFailedError(f"CSV files are limited to {MAX_ROWS:,} rows.")
            raw = dict(zip(header, row, strict=False))
            if len(row) != len(header):
                result.reject(line, f"expected {len(header)} fields, found {len(row)}", raw)
                continue
            record, error = _parse_row(spec, raw, today)
            if error:
                result.reject(line, error, raw)
                continue
            key = tuple(record[k] for k in spec.key)
            if key in seen:
                first_line, first_record = seen[key]
                if first_record == record:
                    result.reject(line, f"duplicate of line {first_line}", raw)
                else:
                    conflicting.add(key)
                    result.reject(line, f"conflicts with line {first_line} (same key, different values)", raw)
                continue
            record["_line"] = line
            seen[key] = (line, {k: v for k, v in record.items() if k != "_line"})
            result.records.append(record)
    except csv.Error as exc:
        raise ValidationFailedError(f"Malformed CSV near line {line}: {exc}") from exc

    if conflicting:
        # When two rows disagree we cannot know which is right, so neither is loaded.
        kept = []
        for record in result.records:
            key = tuple(record[k] for k in spec.key)
            if key in conflicting:
                result.reject(record["_line"], "conflicting duplicate key; neither value was loaded")
            else:
                kept.append(record)
        result.records = kept

    if kind == "holdings":
        _check_holding_totals(result)
    return result


def _parse_row(spec: KindSpec, raw: dict, today: date) -> tuple[dict, str | None]:
    record: dict[str, Any] = {}
    for column in spec.columns:
        value = (raw.get(column.name) or "").strip()
        if not value:
            if column.required:
                return {}, f"{column.name}: value is missing"
            record[column.name] = None
            continue
        try:
            record[column.name] = column.parser(value)
        except ValueError as exc:
            return {}, f"{column.name}: {exc}"
    for name in ("date", "as_of_date", "month"):
        if isinstance(record.get(name), date) and record[name] > today:
            return {}, f"{name}: {record[name].isoformat()} is in the future"
    return record, None


def _check_holding_totals(result: ParsedFile) -> None:
    totals: dict[tuple[str, date], Decimal] = {}
    for record in result.records:
        key = (record["scheme_code"], record["as_of_date"])
        totals[key] = totals.get(key, Decimal(0)) + record["weight_pct"]
    rejected_groups = {key for key, total in totals.items() if total > Decimal("100.5")}
    for (code, as_of), total in sorted(totals.items()):
        if (code, as_of) in rejected_groups:
            continue
        if total < Decimal(95):
            result.warnings.append(
                f"{code} holdings on {as_of.isoformat()} sum to {total}% (less than 95%); "
                "the remainder is not described."
            )
    if rejected_groups:
        kept = []
        for record in result.records:
            key = (record["scheme_code"], record["as_of_date"])
            if key in rejected_groups:
                result.reject(record["_line"],
                              f"holdings for {key[0]} on {key[1].isoformat()} sum to {totals[key]}% (> 100%)")
            else:
                kept.append(record)
        result.records = kept


def flag_level_outliers(
    records: list[dict], existing: dict[str, dict[date, Decimal]], *, code_field: str, value_field: str,
    threshold: float = 0.20,
) -> list[str]:
    """Warns about day-over-day moves larger than ``threshold``.

    Large moves can be genuine market events, so rows are kept; the warning
    lets a reviewer confirm them.
    """
    warnings: list[str] = []
    by_code: dict[str, dict[date, Decimal]] = {}
    for record in records:
        by_code.setdefault(record[code_field], {})[record["date"]] = record[value_field]
    for code, new_values in by_code.items():
        merged = dict(existing.get(code, {}))
        merged.update(new_values)
        ordered = sorted(merged.items())
        for (prev_day, prev), (day, value) in zip(ordered, ordered[1:], strict=False):
            if day not in new_values and prev_day not in new_values:
                continue
            change = float(value / prev - 1)
            if abs(change) > threshold:
                warnings.append(
                    f"{code}: {change:+.1%} change from {prev_day.isoformat()} to {day.isoformat()} "
                    "exceeds the outlier threshold; row kept, please verify."
                )
    return warnings
