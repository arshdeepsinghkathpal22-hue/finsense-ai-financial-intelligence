#!/usr/bin/env sh
# FinSense AI launcher for Linux and macOS (Docker Compose).
#
#   ./run.sh                 build (first time) and start everything, then open the browser
#   ./run.sh stop            stop the containers (data is kept)
#   ./run.sh status          show container status
#   ./run.sh logs            follow the logs (Ctrl+C to stop following)
#   ./run.sh create-admin you@example.com   create an administrator (asks for a password)
#   ./run.sh promote you@example.com        make an already registered user an administrator
#   ./run.sh reset           stop and DELETE all data (database, uploads, models)
#   ./run.sh native          run WITHOUT Docker (needs Python 3.12/3.13, Node 20.19+,
#                            PostgreSQL + pgvector prepared with db/manual-setup.sql)
#
# On first start it creates .env from .env.example with random database
# passwords. No credentials are stored in this script.
set -eu

cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

compose() { docker compose "$@"; }

check_docker() {
    command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
    docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required ('docker compose'). Update Docker."
    docker info >/dev/null 2>&1 || fail "Docker is installed but not running (or your user cannot access it). Start Docker and try again."
}

random_secret() {
    # 48 hex characters from the OS random source (URL-safe for connection strings).
    od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

ensure_env() {
    if [ -f .env ]; then
        return
    fi
    [ -f .env.example ] || fail ".env.example is missing; re-extract the project."
    owner_pw=$(random_secret)
    app_pw=$(random_secret)
    [ ${#owner_pw} -eq 48 ] && [ ${#app_pw} -eq 48 ] || fail "Could not generate random passwords."
    umask 077
    sed -e "s/change-me-owner-password/${owner_pw}/g" -e "s/change-me-app-password/${app_pw}/g" \
        .env.example > .env.tmp
    mv .env.tmp .env
    say "Created .env with newly generated database passwords (keep this file private)."
}

env_value() {
    # Prints the value of KEY from .env (empty if absent).
    sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d '\r'
}

http_ok() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS -o /dev/null --max-time 5 "$1" 2>/dev/null
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O /dev/null -T 5 "$1" 2>/dev/null
    else
        return 1
    fi
}

wait_for_app() {
    url="$1"
    say "Waiting for FinSense AI to become ready (the first build can take 10-20 minutes)..."
    elapsed=0
    while [ "$elapsed" -lt 1800 ]; do
        if http_ok "$url/api/v1/health/ready"; then
            return 0
        fi
        if compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null \
            | grep -q '^bootstrap exited [1-9]'; then
            compose logs --tail=60 bootstrap >&2 || true
            fail "Database setup failed (see the bootstrap log above)."
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    return 1
}

open_browser() {
    [ "${NO_BROWSER:-0}" = "1" ] && return
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$1" >/dev/null 2>&1 || true
    fi
}

start() {
    check_docker
    ensure_env
    port=$(env_value WEB_PORT)
    url="http://localhost:${port:-8080}"
    say "Building and starting containers..."
    compose up --build -d || fail "docker compose could not start the stack (see the messages above)."
    if wait_for_app "$url"; then
        say ""
        say "FinSense AI is running at $url"
        say "Register an account in the browser, or create an administrator with:"
        say "  ./run.sh create-admin you@example.com"
        say "All bundled funds and documents are SYNTHETIC demonstration data."
        open_browser "$url"
    else
        compose ps >&2 || true
        fail "The app did not become ready in time. Check './run.sh logs'."
    fi
}

command_name="${1:-start}"
case "$command_name" in
    start|up) start ;;
    stop|down) check_docker; compose down ;;
    restart) check_docker; compose down; start ;;
    status|ps) check_docker; compose ps ;;
    logs) check_docker; compose logs -f --tail=200 ;;
    create-admin)
        check_docker
        [ $# -ge 2 ] || fail "Usage: ./run.sh create-admin you@example.com [\"Your Name\"]"
        compose exec api python -m app.cli create-admin --email "$2" --name "${3:-Administrator}"
        ;;
    promote)
        check_docker
        [ $# -ge 2 ] || fail "Usage: ./run.sh promote you@example.com"
        compose exec api python -m app.cli create-admin --email "$2" --promote
        ;;
    reset)
        check_docker
        printf 'This permanently deletes the database, uploads and trained models. Type "delete" to continue: '
        read -r answer
        [ "$answer" = "delete" ] || fail "Cancelled."
        compose down -v
        say "All FinSense AI data was deleted. Your .env file was kept."
        ;;
    native)
        shift
        for candidate in python3.13 python3.12 python3; do
            if command -v "$candidate" >/dev/null 2>&1; then
                exec "$candidate" scripts/run_native.py "$@"
            fi
        done
        fail "Python 3.12 or 3.13 is required for the native mode: https://www.python.org/downloads/"
        ;;
    help|-h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) fail "Unknown command '$command_name'. Try ./run.sh help" ;;
esac
