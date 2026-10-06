"""Migrations, authentication, sessions and platform endpoints."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.services import auth_service
from tests.conftest import APP_URL, PASSWORD, ApiClient, register_and_login


def test_migrations_created_schema_and_runtime_role_is_least_privilege(database):
    tables = set(inspect(create_engine(APP_URL)).get_table_names())
    assert {"users", "funds", "nav_observations", "documents", "document_chunks", "portfolios", "ml_models",
            "audit_events", "alembic_version"} <= tables
    engine = create_engine(APP_URL)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")).scalar() == 1
        try:
            conn.execute(text("CREATE TABLE should_fail (id int)"))
            created = True
        except Exception:
            created = False
    engine.dispose()
    assert not created, "the runtime role must not be able to create tables"


def test_health_endpoints(anon_client):
    assert anon_client.get("/api/v1/health").json() == {"status": "ok"}
    assert anon_client.get("/api/v1/health/ready").json()["database"] == "ok"


def test_openapi_schema_is_generated(anon_client):
    schema = anon_client.get("/api/openapi.json").json()
    assert schema["info"]["title"] == "FinSense AI API"
    assert "/api/v1/assistant/query" in schema["paths"] and "/api/v1/portfolios/{portfolio_id}/optimize" in schema["paths"]


def test_register_login_me_logout(app):
    client = register_and_login(app)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "user"
    assert "password_hash" not in me.text
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_cookie_flags(app):
    client = ApiClient(TestClient(app))
    email = "cookie-check@example.com"
    client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": "C"})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("fs_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("fs_csrf="))
    assert "HttpOnly" in session_cookie and "samesite=lax" in session_cookie.lower()
    assert "HttpOnly" not in csrf_cookie


def test_duplicate_registration_and_weak_password(app, anon_client):
    email = "dupe@example.com"
    first = anon_client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "display_name": "D"})
    assert first.status_code == 201
    again = anon_client.post("/api/v1/auth/register", json={"email": email.upper(), "password": PASSWORD, "display_name": "D"})
    assert again.status_code == 409
    weak = anon_client.post("/api/v1/auth/register", json={"email": "weak@example.com", "password": "password", "display_name": "W"})
    assert weak.status_code == 422 and weak.json()["error"]["details"]["problems"]


def test_wrong_password_is_rejected_without_revealing_accounts(app, anon_client):
    register_and_login(app, email="known@example.com")
    known = anon_client.post("/api/v1/auth/login", json={"email": "known@example.com", "password": "Wrong-Pass-123"})
    unknown = anon_client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "Wrong-Pass-123"})
    assert known.status_code == unknown.status_code == 401
    assert known.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_change_password_revokes_sessions(app):
    client = register_and_login(app)
    second = ApiClient(TestClient(app))
    login = second.post("/api/v1/auth/login", json={"email": client.user["email"], "password": PASSWORD})
    second.csrf = login.json()["csrf_token"]
    changed = client.post("/api/v1/auth/change-password",
                          json={"current_password": PASSWORD, "new_password": "Brand-New-Pass-42"})
    assert changed.status_code == 204
    assert second.get("/api/v1/auth/me").status_code == 401
    relogin = ApiClient(TestClient(app)).post("/api/v1/auth/login",
                                              json={"email": client.user["email"], "password": "Brand-New-Pass-42"})
    assert relogin.status_code == 200


def test_logout_all(app):
    client = register_and_login(app)
    other = ApiClient(TestClient(app))
    other.post("/api/v1/auth/login", json={"email": client.user["email"], "password": PASSWORD})
    assert client.post("/api/v1/auth/logout-all").status_code == 204
    assert other.get("/api/v1/auth/me").status_code == 401


def test_profile_preferences(user_client):
    response = user_client.patch("/api/v1/auth/me", json={"display_name": "Analyst",
                                                         "preferences": {"risk_free_rate": 0.07, "risk_profile": "moderate"}})
    assert response.status_code == 200
    assert response.json()["preferences"] == {"risk_free_rate": 0.07, "risk_profile": "moderate"}
    bad = user_client.patch("/api/v1/auth/me", json={"preferences": {"risk_free_rate": 5}})
    assert bad.status_code == 422


def test_password_reset_unavailable_without_email(anon_client):
    response = anon_client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    assert response.status_code == 503
    assert "not configured" in response.json()["error"]["message"]


def test_password_reset_flow_with_email(app, db, monkeypatch):
    from app.config import get_settings

    client = register_and_login(app)
    settings = get_settings().model_copy(update={"smtp_host": "smtp.test", "smtp_from": "noreply@example.com"})
    sent = {}
    monkeypatch.setattr(auth_service, "send_email", lambda s, *, to, subject, body: sent.update(to=to, body=body))
    auth_service.request_password_reset(db, settings, client.user["email"])
    token = sent["body"].split("token=")[1].split()[0]
    anon = ApiClient(TestClient(app))
    assert anon.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Reset-Pass-777"}).status_code == 204
    assert anon.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "Reset-Pass-888"}).status_code == 422
    assert anon.post("/api/v1/auth/login", json={"email": client.user["email"], "password": "Reset-Pass-777"}).status_code == 200


def test_system_status_reports_configuration(user_client):
    status = user_client.get("/api/v1/system/status").json()
    assert status["llm"]["configured"] is False
    assert status["embeddings"]["dimensions"] == 256
    assert status["data"]["synthetic_funds"] == 10 and status["notice"]
