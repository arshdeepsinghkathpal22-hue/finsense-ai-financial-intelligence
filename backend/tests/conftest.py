"""Shared test fixtures.

Integration tests run against a real PostgreSQL + pgvector database. A
throwaway database (``<name>_test``) is created with the *owner* role,
migrated with Alembic, granted to the least-privilege *runtime* role, and
seeded with the synthetic demo data. The application under test connects
as the runtime role, exactly as in production.

Connection settings come from the same variables as the app:
``MIGRATION_DATABASE_URL`` (owner, needs CREATEDB) and ``DATABASE_URL``
(runtime role). Override with ``TEST_OWNER_DATABASE_URL`` /
``TEST_APP_DATABASE_URL`` if needed.
"""

from __future__ import annotations

import os
import secrets
import tempfile
import uuid
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="finsense-tests-"))
_BACKEND = Path(__file__).resolve().parents[1]


# Only the database connection settings are taken from the project's .env files;
# everything else uses the application defaults so local configuration (for
# example COOKIE_SECURE or RISK_FREE_RATE) cannot change what the tests check.
_DOTENV_KEYS = {"DATABASE_URL", "MIGRATION_DATABASE_URL", "TEST_DATABASE_NAME", "TEST_OWNER_DATABASE_URL",
                "TEST_APP_DATABASE_URL"}


def _load_dotenv() -> None:
    """Reads the database settings from the project .env files without overriding the environment."""
    for path in (_BACKEND.parent / ".env", _BACKEND / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in _DOTENV_KEYS:
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()
os.environ["FINSENSE_IGNORE_ENV_FILE"] = "1"


def _derive(url: str, database: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).set(database=database).render_as_string(hide_password=False)


# End-to-end tests can target an already running deployment instead of a test database.
E2E_BASE_URL = os.environ.get("FINSENSE_E2E_BASE_URL", "").rstrip("/")

_owner_base = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
_app_base = os.environ.get("DATABASE_URL")
if (not _owner_base or not _app_base) and not E2E_BASE_URL:
    raise RuntimeError("Set DATABASE_URL and MIGRATION_DATABASE_URL (see .env.example) to run the tests, "
                       "or FINSENSE_E2E_BASE_URL to run only the end-to-end tests against a live server.")
TEST_DB_NAME = os.environ.get("TEST_DATABASE_NAME", "finsense_test")
if _owner_base and _app_base:
    OWNER_URL = os.environ.get("TEST_OWNER_DATABASE_URL") or _derive(_owner_base, TEST_DB_NAME)
    APP_URL = os.environ.get("TEST_APP_DATABASE_URL") or _derive(_app_base, TEST_DB_NAME)
    MAINTENANCE_URL = _derive(_owner_base, "postgres")
else:  # live end-to-end mode only; database fixtures are unavailable
    OWNER_URL = APP_URL = MAINTENANCE_URL = "postgresql+psycopg://unused:unused@localhost/unused"

# A recognisable fake secret: tests assert it never leaks into prompts, logs or responses.
FAKE_LLM_KEY = "sk-test-" + secrets.token_hex(12)

os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": APP_URL,
    "MIGRATION_DATABASE_URL": OWNER_URL,
    "UPLOAD_DIR": str(_TMP / "uploads"),
    "MODEL_DIR": str(_TMP / "models"),
    "CACHE_DIR": str(_TMP / "cache"),
    "LLM_PROVIDER": "none",
    "LLM_API_KEY": FAKE_LLM_KEY,
    "CORS_ORIGINS": "http://localhost:5173",
    "SMTP_HOST": "",
    "AMFI_ENABLED": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402


def _recreate_database() -> None:
    engine = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    engine.dispose()


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    from app.config import BACKEND_DIR

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["database_url"] = OWNER_URL
    command.upgrade(config, "head")


def _grant_runtime_role() -> None:
    from sqlalchemy.engine import make_url

    app_user = make_url(APP_URL).username
    owner_user = make_url(OWNER_URL).username
    if app_user == owner_user:
        return
    engine = create_engine(OWNER_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{TEST_DB_NAME}" TO "{app_user}"'))
        conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{app_user}"'))
        conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{app_user}"'))
        conn.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_user}"'))
        conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    engine.dispose()


@pytest.fixture(scope="session")
def database():
    """Fresh, migrated, seeded database shared by the whole test session."""
    _recreate_database()
    _migrate()
    _grant_runtime_role()
    from app import cli

    assert cli.cmd_seed(None) == 0
    assert cli.cmd_ingest_samples(None) == 0
    yield
    from app.db import get_engine

    get_engine().dispose()


@pytest.fixture(scope="session")
def app(database):
    from app.main import create_app

    return create_app(get_settings())


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def db(database):
    from app.db import get_sessionmaker

    session = get_sessionmaker()()
    yield session
    session.rollback()
    session.close()


class ApiClient:
    """TestClient wrapper that remembers the CSRF token after login."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.csrf: str | None = None
        self.user: dict | None = None

    def _headers(self, headers: dict | None) -> dict:
        merged = dict(headers or {})
        if self.csrf:
            merged.setdefault("X-CSRF-Token", self.csrf)
        return merged

    def get(self, url: str, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url: str, headers: dict | None = None, **kwargs):
        return self.client.post(url, headers=self._headers(headers), **kwargs)

    def put(self, url: str, headers: dict | None = None, **kwargs):
        return self.client.put(url, headers=self._headers(headers), **kwargs)

    def patch(self, url: str, headers: dict | None = None, **kwargs):
        return self.client.patch(url, headers=self._headers(headers), **kwargs)

    def delete(self, url: str, headers: dict | None = None, **kwargs):
        return self.client.delete(url, headers=self._headers(headers), **kwargs)


PASSWORD = "Test-Password-123"


def register_and_login(app, *, admin: bool = False, email: str | None = None) -> ApiClient:
    client = ApiClient(TestClient(app))
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD,
                                                          "display_name": "Test User"})
    assert response.status_code == 201, response.text
    if admin:
        from sqlalchemy import update

        from app.db import get_sessionmaker
        from app.models import User

        with get_sessionmaker()() as session:
            session.execute(update(User).where(User.email == email).values(role="admin"))
            session.commit()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.csrf = login.json()["csrf_token"]
    client.user = login.json()["user"]
    return client


@pytest.fixture
def user_client(app) -> ApiClient:
    return register_and_login(app)


@pytest.fixture
def other_client(app) -> ApiClient:
    return register_and_login(app)


@pytest.fixture
def admin_client(app) -> ApiClient:
    return register_and_login(app, admin=True)


@pytest.fixture
def anon_client(app) -> ApiClient:
    return ApiClient(TestClient(app))


@pytest.fixture
def fund_ids(db) -> dict[str, int]:
    from sqlalchemy import select

    from app.models import Fund

    return {code: fid for fid, code in db.execute(select(Fund.id, Fund.scheme_code)).all()}


class FakeLLM:
    """Deterministic stand-in for a language model; records every prompt."""

    name = "fake/test-model"

    def __init__(self, reply: str | None = None, plan: str | None = None) -> None:
        self.reply = reply
        self.plan = plan
        self.calls: list[tuple[str, list]] = []

    def complete(self, system, messages, *, max_tokens, temperature=0.0):  # noqa: ANN001
        self.calls.append((system, messages))
        if "You plan which analytics to run" in system:
            return self.plan if self.plan is not None else '{"tools": [], "use_documents": true}'
        if self.reply is not None:
            return self.reply
        return "The exit load is 0.25% if redeemed within 30 days [S1]."


@pytest.fixture
def fake_llm():
    return FakeLLM
