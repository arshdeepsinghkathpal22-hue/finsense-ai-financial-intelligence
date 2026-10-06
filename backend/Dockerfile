# FinSense AI - API image (FastAPI + Uvicorn), runs as an unprivileged user.

# Stage 1: collect wheels for every pinned dependency. On x86_64 all of them
# are published as binary wheels; on arm64 (e.g. Apple Silicon) a few have
# no Linux/arm64 wheel and are compiled here, so the final image needs no compiler.
FROM python:3.13-slim-bookworm AS wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Stage 2: runtime image.
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 finsense \
    && useradd --system --uid 10001 --gid finsense --home-dir /app --shell /usr/sbin/nologin finsense

COPY requirements.txt ./
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt

COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY sample_data ./sample_data

# Writable state (uploads, trained models, caches) lives in a volume at /app/var.
RUN mkdir -p /app/var/uploads /app/var/models /app/var/cache \
    && chown -R 10001:10001 /app/var

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=4)"]

# Migrations and demo data are applied by the one-shot `bootstrap` service in
# docker-compose.yml, which has the owner credentials; this process only
# receives the least-privilege runtime role.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
