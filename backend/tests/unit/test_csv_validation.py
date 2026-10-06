"""CSV parsing and validation rules (no database)."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import PayloadTooLargeError, ValidationFailedError
from app.services.ingestion import validation

TODAY = date(2026, 10, 5)


def parse(kind, text):
    return validation.parse_csv(kind, text, today=TODAY)


def test_valid_rows_are_normalised():
    result = parse("nav", "Scheme_Code,Date,NAV\nA-1,2026-09-30,\"1,234.50\"\nA-1,29-09-2026,12.5\n")
    assert [r["date"] for r in result.records] == [date(2026, 9, 30), date(2026, 9, 29)]
    assert result.records[0]["nav"] == Decimal("1234.50")
    assert result.rejected == []


def test_rejections_carry_line_numbers_and_reasons():
    text = (
        "scheme_code,date,nav\n"
        "A,2026-09-30,10\n"
        "A,2026-09-30,10\n"        # duplicate
        "B,2026-09-30,\n"          # missing
        "C,2026-09-31,10\n"        # invalid date
        "D,2026-09-30,-1\n"        # negative
        "E,2027-01-01,10\n"        # future
    )
    result = parse("nav", text)
    reasons = {r["line"]: r["reason"] for r in result.rejected}
    assert "duplicate of line 2" in reasons[3]
    assert "missing" in reasons[4]
    assert "not a valid date" in reasons[5]
    assert "positive" in reasons[6]
    assert "future" in reasons[7]
    assert len(result.records) == 1


def test_conflicting_duplicates_are_both_rejected():
    result = parse("nav", "scheme_code,date,nav\nA,2026-09-30,10\nA,2026-09-30,11\n")
    assert result.records == []
    assert len(result.rejected) == 2


def test_missing_header_and_unknown_columns():
    with pytest.raises(ValidationFailedError) as exc:
        parse("nav", "scheme_code,nav\nA,1\n")
    assert "date" in exc.value.message
    result = parse("nav", "scheme_code,date,nav,comment\nA,2026-09-30,1,hello\n")
    assert any("comment" in w for w in result.warnings)


def test_unknown_kind_rejected():
    with pytest.raises(ValidationFailedError):
        parse("not-a-kind", "a\n1\n")


def test_file_level_checks():
    with pytest.raises(ValidationFailedError):
        validation.decode_csv_bytes(b"\x00\x01binary", max_bytes=1000)
    with pytest.raises(ValidationFailedError):
        validation.decode_csv_bytes("caf\xe9".encode("latin-1"), max_bytes=1000)
    with pytest.raises(PayloadTooLargeError):
        validation.decode_csv_bytes(b"a" * 2000, max_bytes=1000)
    with pytest.raises(ValidationFailedError):
        validation.decode_csv_bytes(b"   ", max_bytes=1000)
    assert validation.decode_csv_bytes("﻿a,b\n".encode(), max_bytes=1000) == "a,b\n"


def test_holdings_weight_totals():
    over = "scheme_code,as_of_date,holding_name,sector,asset_type,weight_pct\nA,2026-09-30,X,S,equity,60\nA,2026-09-30,Y,S,equity,50\n"
    result = parse("holdings", over)
    assert result.records == []
    assert all("> 100%" in r["reason"] for r in result.rejected)
    under = "scheme_code,as_of_date,holding_name,sector,asset_type,weight_pct\nA,2026-09-30,X,S,equity,60\n"
    result = parse("holdings", under)
    assert len(result.records) == 1
    assert any("less than 95%" in w for w in result.warnings)


def test_outliers_are_flagged_not_dropped():
    records = [{"scheme_code": "A", "date": date(2026, 9, 30), "nav": Decimal("150")}]
    existing = {"A": {date(2026, 9, 29): Decimal("100")}}
    warnings = validation.flag_level_outliers(records, existing, code_field="scheme_code", value_field="nav")
    assert len(warnings) == 1 and "+50.0%" in warnings[0]
    assert validation.flag_level_outliers(
        [{"scheme_code": "A", "date": date(2026, 9, 30), "nav": Decimal("101")}], existing,
        code_field="scheme_code", value_field="nav") == []


def test_codes_and_percentages_validated():
    with pytest.raises(ValueError):
        validation.parse_code("bad code with spaces")
    with pytest.raises(ValueError):
        validation.parse_percent("120")
    assert validation.parse_percent("12.5%") == Decimal("12.5")
