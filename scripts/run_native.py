"""FinSense AI native launcher (no Docker): ./run.sh native  or  run.bat native

Prerequisites you install yourself:
  * Python 3.12 or 3.13 and Node.js 20.19+ (with npm)
  * PostgreSQL 14+ with the pgvector extension, prepared once with
    db/manual-setup.sql (see README, "Manual setup without Docker")

What it does, in order (safe to re-run):
  1. creates .env from .env.example with random passwords if .env does not exist
  2. creates backend/.venv and installs the pinned Python dependencies
  3. checks the database connection and applies migrations + demo data (idempotent)
  4. installs the frontend dependencies and builds the production bundle
  5. starts the API on 127.0.0.1:8000 and the web app on http://127.0.0.1:4173
     (the web server proxies /api to the API), then waits until you press Ctrl+C
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
API_PORT = int(os.environ.get("FINSENSE_API_PORT", "8000"))
WEB_PORT = int(os.environ.get("FINSENSE_WEB_PORT", "4173"))
IS_WINDOWS = os.name == "nt"


def say(message: str) -> None:
    print(message, flush=True)


def fail(message: str) -> None:
    print(f"\nError: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def run(command: list[str], cwd: Path, env: dict | None = None) -> None:
    say(f"$ {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd, env=env, check=False)  # noqa: S603
    if result.returncode != 0:
        fail(f"'{' '.join(command[:3])} ...' failed with exit code {result.returncode} (see the output above).")


def check_prerequisites() -> tuple[str, str]:
    if not ((3, 12) <= sys.version_info[:2] <= (3, 13)):
        fail(f"Python 3.12 or 3.13 is required (this is {sys.version.split()[0]}).")
    npm = shutil.which("npm.cmd" if IS_WINDOWS else "npm") or shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        fail("Node.js 20.19+ with npm is required: https://nodejs.org/")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()  # noqa: S603
    major, minor = (int(x) for x in version.lstrip("v").split(".")[:2])
    if (major, minor) < (20, 19):
        fail(f"Node.js 20.19 or newer is required (found {version}).")
    return npm, node


def ensure_env() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        say("Using the existing .env file.")
        return
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    owner_pw, app_pw = secrets.token_hex(24), secrets.token_hex(24)
    text = template.replace("change-me-owner-password", owner_pw).replace("change-me-app-password", app_pw)
    env_file.write_text(text, encoding="utf-8")
    if not IS_WINDOWS:
        env_file.chmod(0o600)
    say("Created .env with new random database passwords.")
    say("Prepare PostgreSQL once with these passwords (values are in .env: POSTGRES_PASSWORD, APP_DB_PASSWORD):")
    say('  psql -U postgres -h localhost -v owner_password="<POSTGRES_PASSWORD>" '
        '-v app_password="<APP_DB_PASSWORD>" -f db/manual-setup.sql')
    say("Then run this launcher again.")
    raise SystemExit(0)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def ensure_backend() -> None:
    requirements = BACKEND / "requirements.txt"
    stamp = VENV / ".requirements.sha256"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not venv_python().exists():
        say("Creating the backend virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV)], BACKEND)
    if not stamp.exists() or stamp.read_text().strip() != digest:
        say("Installing backend dependencies (first run takes a few minutes)...")
        run([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)],
            BACKEND)
        stamp.write_text(digest)


def child_env() -> dict:
    env = dict(os.environ)
    env.setdefault("APP_ENV", "development")
    env["PUBLIC_APP_URL"] = f"http://127.0.0.1:{WEB_PORT}"
    env["CORS_ORIGINS"] = f"http://127.0.0.1:{WEB_PORT},http://localhost:{WEB_PORT}"
    env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{API_PORT}"
    return env


def bootstrap_database(env: dict) -> None:
    say("Checking the database, applying migrations and loading demo data...")
    probe = subprocess.run(  # noqa: S603
        [str(venv_python()), "-c",
         "from sqlalchemy import create_engine, text; from app.config import get_settings; "
         "s = get_settings(); u = (s.migration_database_url or s.database_url).get_secret_value(); "
         "create_engine(u).connect().execute(text('select 1'))"],
        cwd=BACKEND, env=env, capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        last = (probe.stderr.strip().splitlines() or ["unknown error"])[-1]
        fail("Cannot connect to PostgreSQL with the settings in .env "
             f"({last[:200]}).\nStart PostgreSQL and make sure db/manual-setup.sql was run with the passwords "
             "from .env (see README, 'Manual setup without Docker').")
    run([str(venv_python()), "-m", "app.cli", "bootstrap"], BACKEND, env)


def ensure_frontend(npm: str) -> None:
    lock = FRONTEND / "package-lock.json"
    stamp = FRONTEND / "node_modules" / ".package-lock.sha256"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if not stamp.exists() or stamp.read_text().strip() != digest:
        say("Installing frontend dependencies...")
        run([npm, "ci", "--no-audit", "--no-fund"], FRONTEND)
        stamp.write_text(digest)
    say("Building the frontend...")
    run([npm, "run", "build"], FRONTEND)


def wait_for(url: str, seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 (local URL)
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def serve(node: str, env: dict) -> None:
    # Vite is started with node directly (not through npm) so stopping it stops the real server process.
    vite = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    processes = [
        subprocess.Popen([str(venv_python()), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",  # noqa: S603
                          "--port", str(API_PORT)], cwd=BACKEND, env=env),
        subprocess.Popen([node, str(vite), "preview", "--port", str(WEB_PORT), "--strictPort",  # noqa: S603
                          "--host", "127.0.0.1"], cwd=FRONTEND, env=env),
    ]

    def stop(*_: object) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    url = f"http://127.0.0.1:{WEB_PORT}"
    try:
        if not wait_for(f"http://127.0.0.1:{API_PORT}/api/v1/health/ready", 90):
            stop()
            fail("The API did not start (see the messages above).")
        if not wait_for(f"http://127.0.0.1:{WEB_PORT}/api/v1/health", 60):
            stop()
            fail("The web server did not start (see the messages above).")
        say(f"\nFinSense AI is running at {url}  (API docs: http://127.0.0.1:{API_PORT}/api/docs)")
        say("Register an account in the browser. Press Ctrl+C to stop.")
        if os.environ.get("NO_BROWSER") != "1":
            webbrowser.open(url)
        while all(process.poll() is None for process in processes):
            time.sleep(1)
        fail("A server process exited unexpectedly (see the messages above).")
    except KeyboardInterrupt:
        say("\nStopping...")
    finally:
        stop()


def main() -> None:
    npm, node = check_prerequisites()
    ensure_env()
    ensure_backend()
    env = child_env()
    bootstrap_database(env)
    ensure_frontend(npm)
    if "--no-serve" in sys.argv:
        say("Setup complete (--no-serve given, servers not started).")
        return
    serve(node, env)


if __name__ == "__main__":
    main()
