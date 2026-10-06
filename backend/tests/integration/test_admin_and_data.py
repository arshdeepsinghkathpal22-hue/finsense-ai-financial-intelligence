"""Administrator data management: CSV imports, data status, audit log, user management, AMFI adapter."""

import io
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from app.core.errors import ServiceUnavailableError, ValidationFailedError
from app.models import Fund, NavObservation
from app.services.ingestion import amfi

SAMPLES = Path(__file__).resolve().parents[2] / "sample_data" / "csv"


def _import(client, kind: str, content: bytes, filename: str = "upload.csv", source: str = "synthetic-demo"):
    return client.post(f"/api/v1/admin/imports/{kind}", data={"source_code": source},
                       files={"file": (filename, io.BytesIO(content), "text/csv")})


def test_nav_import_reports_row_level_problems_and_is_idempotent(admin_client, fund_ids):
    content = (SAMPLES / "nav_with_errors.csv").read_bytes()
    first = _import(admin_client, "nav", content, "nav_with_errors.csv")
    assert first.status_code == 200
    batch = first.json()
    assert batch["status"] == "completed" and batch["rows_total"] == 8
    reasons = " | ".join(r["reason"] for r in batch["rejected_rows"])
    for fragment in ("duplicate", "unknown scheme_code", "date"):
        assert fragment in reasons, reasons
    assert batch["rows_rejected"] >= 5
    assert any("outlier" in w for w in batch["warnings"])  # implausible jump is kept but flagged

    again = _import(admin_client, "nav", content, "nav_with_errors.csv").json()
    assert again["rows_inserted"] == 0 and again["rows_updated"] == 0
    assert any("identical file was already imported" in w for w in again["warnings"])

    # Restore the value the error sample deliberately distorted, exercising the "update" path.
    original = [line for line in (SAMPLES / "nav.csv").read_text().splitlines()
                if line.startswith("FS-IDX-006,2026-09-30,")]
    restored = _import(admin_client, "nav", ("scheme_code,date,nav\n" + original[0] + "\n").encode()).json()
    assert restored["rows_updated"] == 1

    history = admin_client.get("/api/v1/admin/imports").json()["items"]
    assert any(item["id"] == batch["id"] for item in history)
    assert admin_client.get(f"/api/v1/admin/imports/{batch['id']}").json()["rows_total"] == 8


def test_real_and_synthetic_data_are_never_mixed(admin_client):
    content = b"scheme_code,date,nav\nFS-LC-001,2026-10-01,100.0\n"
    batch = _import(admin_client, "nav", content, source="csv-import").json()
    assert batch["rows_inserted"] == 0 and batch["rows_rejected"] == 1
    assert "synthetic" in batch["rejected_rows"][0]["reason"]


def test_import_validation(admin_client, user_client):
    assert _import(admin_client, "nav", b"a,b\n1,2\n", "notes.txt").status_code == 422
    missing_columns = _import(admin_client, "nav", b"scheme_code,nav\nFS-LC-001,1\n")
    assert missing_columns.status_code == 422 and "date" in str(missing_columns.json()["error"])
    assert _import(user_client, "nav", b"scheme_code,date,nav\n").status_code == 403


def test_admin_overview_endpoints(admin_client, user_client):
    status = admin_client.get("/api/v1/admin/data-status").json()
    assert status["funds"] and any(f["scheme_code"] == "FS-LC-001" for f in status["funds"])
    audit = admin_client.get("/api/v1/admin/audit").json()["items"]
    assert any(e["event_type"] == "auth.login" for e in audit)
    users = admin_client.get("/api/v1/admin/users").json()["items"]
    assert any(u["email"] == user_client.user["email"] for u in users)
    diag = admin_client.post("/api/v1/admin/rag/diagnostics", json={"query": "FS-DB-007 exit load"}).json()
    assert diag["candidates"] and "vector_rank" in diag["candidates"][0]


def test_admin_can_deactivate_user(admin_client, user_client):
    target = user_client.user["id"]
    assert admin_client.patch(f"/api/v1/admin/users/{target}", json={"is_active": False}).status_code == 200
    assert user_client.get("/api/v1/auth/me").status_code == 401
    assert admin_client.patch(f"/api/v1/admin/users/{admin_client.user['id']}",
                              json={"role": "user"}).status_code in (409, 422)  # cannot demote yourself


NAVALL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

Example Mutual Fund
100001;INF000000001;-;Example Large Cap Fund - Direct Plan - Growth;123.4567;02-Oct-2026
100002;INF000000002;INF000000003;Example Large Cap Fund - Direct Plan - IDCW;23.1000;02-Oct-2026
100003;INF000000004;-;Broken Row Fund;N.A.;02-Oct-2026
"""


def test_amfi_parser_skips_headers_and_malformed_rows():
    parsed = amfi.parse_navall(NAVALL)
    assert set(parsed) == {"100001", "100002"}
    assert str(parsed["100001"]["nav"]) == "123.4567" and parsed["100001"]["date"].isoformat() == "2026-10-02"


def test_amfi_register_and_sync_with_mocked_http(db, tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=NAVALL)

    settings = get_settings().model_copy(update={"amfi_enabled": True, "cache_dir": tmp_path})
    transport = httpx.MockTransport(handler)
    fund = amfi.register_scheme(db, settings, "100002", category="Large Cap", asset_class="equity",
                                transport=transport)
    try:
        assert fund.option == "IDCW" and fund.return_basis == "nav_price" and not fund.is_synthetic
        result = amfi.sync_latest_navs(db, settings, transport=transport)
        assert result["tracked"] == 1 and result["updated"][0]["date"] == "2026-10-02"
        assert result["updated"][0]["nav"] == "23.1000"
        assert len(calls) == 1  # the second read came from the on-disk cache
        with pytest.raises(ValidationFailedError):
            amfi.register_scheme(db, settings, "999999", category="x", asset_class="equity", transport=transport)
    finally:  # keep the shared test database limited to the synthetic universe
        db.rollback()
        db.execute(delete(NavObservation).where(NavObservation.fund_id == fund.id))
        db.execute(delete(Fund).where(Fund.id == fund.id))
        db.commit()


def test_amfi_failures_and_disabled_state(admin_client, tmp_path):
    settings = get_settings().model_copy(update={"amfi_enabled": True, "cache_dir": tmp_path})
    failing = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(ServiceUnavailableError) as excinfo:
        amfi.fetch_navall(settings, failing)
    assert "could not be downloaded" in str(excinfo.value)
    disabled = admin_client.post("/api/v1/admin/amfi/sync")
    assert disabled.status_code == 503 and "disabled" in disabled.json()["error"]["message"]


def test_create_admin_command(app, monkeypatch, db):
    import argparse

    from app import cli
    from app.models import User
    from tests.conftest import register_and_login

    monkeypatch.setenv("FINSENSE_ADMIN_PASSWORD", "Cli-Admin-Pass-1")
    assert cli.cmd_create_admin(argparse.Namespace(email="cli-admin@example.com", name="CLI", promote=False)) == 0
    assert db.scalar(select(User.role).where(User.email == "cli-admin@example.com")) == "admin"
    assert cli.cmd_create_admin(argparse.Namespace(email="not-an-email", name="x", promote=False)) == 1

    monkeypatch.delenv("FINSENSE_ADMIN_PASSWORD")  # promoting must not ask for a password
    client = register_and_login(app, email="cli-promote@example.com")
    assert cli.cmd_create_admin(argparse.Namespace(email=client.user["email"], name="x", promote=False)) == 1
    assert cli.cmd_create_admin(argparse.Namespace(email=client.user["email"], name="x", promote=True)) == 0
    assert client.get("/api/v1/admin/users").status_code == 200
