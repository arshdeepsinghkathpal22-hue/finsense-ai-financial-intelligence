# FinSense 

AI-powered financial intelligence, document research (RAG) and portfolio decision support for
mutual-fund investors and analysts.

FinSense AI combines a mutual-fund analytics engine (returns, risk, drawdowns, VaR/CVaR,
benchmark statistics), a portfolio builder with mean-variance optimisation, a what-if simulator,
machine-learning forecasts and anomaly detection with explanations, and a research assistant that
answers questions from your own documents with page-level citations.

> **Decision support only - not investment advice.** Historical metrics and model estimates do not
> guarantee future results. **All bundled funds, prices and documents are SYNTHETIC demonstration
> data** (fictional fund houses, companies and figures). They are labelled as such everywhere in
> the app and must not be used for real investment decisions.

> **About this codebase.** The project was produced with the help of an AI coding assistant and
> verified with the automated checks described in [docs/TEST_REPORT.md](docs/TEST_REPORT.md).
> Review it before relying on it, and read the known limitations below.

**Quick start:** install Docker Desktop, then run `run.bat` (Windows) or `./run.sh` (Linux/macOS)
and open <http://localhost:8080>. A plain-text walkthrough for first-time users is in
[HOW_TO_USE.txt](HOW_TO_USE.txt).

## Contents

1. [Introduction](#1-introduction)
2. [Features actually implemented](#2-features-actually-implemented)
3. [Architecture and component responsibilities](#3-architecture-and-component-responsibilities)
4. [Technology stack](#4-technology-stack)
5. [Prerequisites](#5-prerequisites)
6. [Windows setup](#6-windows-setup)
7. [Linux / macOS setup](#7-linux--macos-setup)
8. [Environment configuration](#8-environment-configuration)
9. [Database initialisation](#9-database-initialisation)
10. [Data import and sample-data generation](#10-data-import-and-sample-data-generation)
11. [Document ingestion and indexing](#11-document-ingestion-and-indexing)
12. [Embedding and LLM configuration](#12-embedding-and-llm-configuration)
13. [Starting the application](#13-starting-the-application)
14. [Running the test suites](#14-running-the-test-suites)
15. [RAG evaluation and interpretation](#15-rag-evaluation-and-interpretation)
16. [Security controls and known limitations](#16-security-controls-and-known-limitations)
17. [Troubleshooting](#17-troubleshooting)
18. [API documentation](#18-api-documentation)
19. [Project directory structure](#19-project-directory-structure)
20. [Responsible deployment](#20-responsible-deployment)

---

## 1. Introduction

Retail investors and analysts usually juggle fund factsheets, scheme documents, NAV histories and
spreadsheets to answer simple questions ("why did this fund get riskier?", "what does my portfolio
look like if mid-caps fall 20%?"). FinSense AI puts these in one place:

* **Analytics you can audit.** Every metric shows its period, frequency, annualisation factor,
  risk-free rate and method (for example "historical VaR, 95%, linear interpolation"). Metrics
  that cannot be computed show *n/a* with the reason instead of a misleading number.
* **Grounded answers.** The research assistant retrieves passages from documents you are allowed
  to read, cites the document and page for every statement, flags conflicting reporting periods,
  and says so when the documents do not contain the answer.
* **Honest models.** Forecasts are always compared with naive baselines on a held-out period;
  when a model does not beat the baseline (the usual case for fund returns) the app says so.

## 2. Features actually implemented

Everything listed here is implemented and covered by automated tests unless marked otherwise.

**Accounts and security**
* Registration, login, logout, logout-everywhere, change password, profile preferences
  (default period, risk-free rate, risk profile). Roles: `user` and `admin`.
* Password reset by e-mail token - implemented and tested with a mocked mail sender; real SMTP
  delivery is **not verified** (no SMTP server was available). Without SMTP settings the reset
  endpoint returns a clear "not configured" error.

**Dashboard** - KPI cards (return, CAGR, volatility, Sharpe, Sortino, beta, maximum drawdown,
VaR/CVaR) for a selected fund or saved portfolio, growth-of-100 versus benchmark, drawdown chart,
data-freshness badge and an assumptions panel.

**Fund explorer** - search and filter by category/asset class, fund detail (scheme facts, AUM,
trailing returns with "not covered" when history is too short, NAV and drawdown charts, holdings
and sector concentration, AUM and SIP-flow history), and side-by-side comparison of 2-5 funds.

**Risk analytics** - full risk report per fund and period: cumulative return, CAGR, annualised
volatility, Sharpe and Sortino ratios, beta, correlation, alpha, tracking error, information ratio,
maximum drawdown with peak/trough/recovery dates, historical and parametric VaR/CVaR, rolling
Sharpe/volatility/beta, return distribution and a correlation matrix across funds.

**Portfolio builder** - create portfolios manually or from CSV, weight validation (must total 100%),
buy-and-hold valuation versus benchmark, risk contributions, concentration (HHI, effective number of
holdings), diversification ratio, CSV export, and **mean-variance optimisation** (SLSQP; objectives
utility / minimum variance / maximum Sharpe; long-only, sum-to-one, min/max weight constraints;
infeasible constraints are rejected with an explanation; Ledoit-Wolf shrinkage when the covariance
matrix is ill-conditioned; efficient frontier; current vs optimised vs equal-weight comparison;
apply the optimised weights in one click).

**What-if simulator** - SIP projection, Monte Carlo SIP (percentile bands), market shock via betas,
volatility stress test, and allocation-change comparison.

**ML forecasting and anomaly detection** - leakage-safe features, chronological train/validation/test
split with purge gaps, models: historical mean, ridge regression and random forest (5, 21 or 63
trading-day horizons), evaluation on the held-out test period against two baselines (historical mean
and zero return), 80% prediction interval with measured coverage, SHAP explanations (global and for
the latest prediction), a model registry with data fingerprints (unchanged data reuses the trained
model), and Isolation-Forest anomaly detection with per-day robust z-score explanations.
An LSTM option is shown as *unavailable* with the reason (not implemented, see limitations).

**Document library and RAG research assistant**
* Upload PDF, TXT or Markdown (private to you; administrators can add shared documents). Type and
  size checks, signature sniffing, duplicate detection, page limits, and parsing in a separate
  resource-limited process with a timeout.
* Page-aware extraction with tables kept intact, repeated header/footer removal, scanned-PDF
  detection (marked *needs OCR* - OCR itself is not implemented), structure-aware chunking
  (headings, tables, sentence packing, overlap).
* Hybrid retrieval: pgvector cosine similarity + PostgreSQL full-text search, fused with weighted
  reciprocal-rank fusion, fund-name boosts, an evidence gate that decides whether the documents can
  answer at all, de-duplication, per-document caps and a context word budget.
* Answers cite `[S1]`-style sources (document, page, section) and `[T1]`-style FinSense calculations;
  forged or out-of-range citations are removed, numbers not found in the evidence are flagged,
  documents from different reporting dates are flagged as conflicting, follow-up questions reuse the
  conversation context, and questions without evidence get an explicit "insufficient evidence" reply.
* Works fully offline: without a language model the assistant quotes the most relevant passages
  verbatim with citations. With a model (Anthropic or any OpenAI-compatible server such as Ollama) it
  writes a summary that is validated the same way. **The real LLM providers were not called during
  testing** (no API key was available); the adapters were tested against mocked HTTP responses and
  the assistant against a deterministic fake model.
* The assistant routes questions to whitelisted analytics tools (fund metrics, risk change, fund
  comparison, forecast, portfolio summary, portfolio optimisation) and combines their output with
  document evidence.

**Administration** - CSV imports for benchmarks, benchmark values, funds, NAVs, AUM, SIP flows and
holdings with row-level rejection reasons, outlier warnings, idempotent re-imports and import
history; data-freshness status; RAG diagnostics (per-stage scores and the exact prompt); on-demand
RAG evaluation; re-index outdated documents; audit log; user activation and roles; optional AMFI
daily-NAV adapter with register/sync controls (**tested with mocked HTTP and a local copy of the file
format; the live AMFI website was not contacted**).

**Not implemented** (deliberately out of scope or blocked in this environment): OCR for scanned
PDFs, LSTM models, real-time market data feeds other than the optional AMFI adapter, brokerage
integration, multi-factor authentication, and horizontal scaling (rate limits and background jobs
are in-process).

## 3. Architecture and component responsibilities

```
 Browser ── http://localhost:8080 ──▶ web (nginx: React build + /api reverse proxy)
                                          │ /api/*
                                          ▼
                                     api (FastAPI, Uvicorn, 1 worker, runtime DB role)
                                          │  SQLAlchemy / psycopg
                                          ▼
                                     db (PostgreSQL 16 + pgvector)
 bootstrap (one-shot, owner DB role): migrations → synthetic data → sample documents
```

| Component | Location | Responsibility |
|---|---|---|
| Frontend | `frontend/src` | React pages, charts, forms; talks only to `/api` on the same origin |
| API layer | `backend/app/api` | Routing, authentication, CSRF, authorisation, validation, rate limits |
| Analytics service | `backend/app/services/analytics` | Return and risk metrics, report assembly, series alignment |
| Portfolio service | `backend/app/services/portfolio` | Portfolio valuation, optimiser, frontier |
| Simulation | `backend/app/services/simulation.py` | SIP, Monte Carlo, shock, stress, allocation change |
| Ingestion | `backend/app/services/ingestion` | CSV validation/import, AMFI adapter |
| ML service | `backend/app/services/ml` | Features, training, evaluation, SHAP, registry, anomalies |
| RAG service | `backend/app/services/rag` | Extraction, chunking, embeddings, indexing, retrieval, generation, citations, evaluation |
| Orchestration | `backend/app/services/assistant` | Question routing to tools and documents, answer assembly |
| Data | `backend/app/models.py`, `backend/alembic` | Relational schema, vector and full-text indexes, migrations |

A detailed description (data model, request flows, RAG pipeline, design decisions) is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 4. Technology stack

| Layer | Technology (pinned versions in lockfiles) |
|---|---|
| Frontend | React 19, TypeScript 6, Vite 8, Tailwind CSS 4, React Router 7, TanStack Query 5, Recharts 3, Vitest + Testing Library |
| Backend | Python 3.12/3.13, FastAPI 0.142, Starlette 1.7, Pydantic 2, SQLAlchemy 2, Alembic, Uvicorn |
| Database | PostgreSQL 16 + pgvector (HNSW cosine index) + built-in full-text search (GIN) |
| Analytics / ML | NumPy, pandas, SciPy (SLSQP), scikit-learn (Ridge, RandomForest, IsolationForest, Ledoit-Wolf), SHAP |
| RAG | pdfplumber (page-aware PDF extraction), WordLlama `l2_supercat` 256-d embeddings (bundled, offline), optional Anthropic / OpenAI-compatible LLM |
| Security | Argon2id password hashing, server-side sessions, CSRF double-submit token, rate limiting, security headers |
| Delivery | Docker Compose (db, bootstrap, api, web/nginx), `run.bat` / `run.sh`, native launcher |

## 5. Prerequisites

**Recommended (Docker):**
* Docker Desktop (Windows 10/11 or macOS) or Docker Engine + Compose v2 (Linux).
* About 6 GB of free disk space and an internet connection for the first build.

**Native mode (without Docker):**
* Python 3.12 or 3.13, Node.js 20.19 or newer (with npm).
* PostgreSQL 14+ with the **pgvector** extension.
* Supported for native installs: Linux x86_64, Windows x64, macOS on Apple Silicon (macOS 14+).
  Intel Macs must use Docker (a scientific dependency no longer publishes Intel-macOS wheels).

## 6. Windows setup

1. Install [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/) and start it
   (wait until it reports that the engine is running).
2. Extract the project ZIP, open the folder, and double-click **`run.bat`** (or run it from a
   Command Prompt in that folder).
3. The first run creates `.env` with random database passwords, builds the images (10-20 minutes the
   first time), migrates the database, loads the demo data and opens <http://localhost:8080>.
4. Register an account in the browser. To get an administrator, run
   `run.bat create-admin you@example.com` (it asks for a password), or promote an account you
   already registered with `run.bat promote you@example.com`.

Other commands: `run.bat stop`, `run.bat status`, `run.bat logs`, `run.bat reset` (deletes data),
`run.bat native` (no Docker; see [section 9](#manual-setup-without-docker)).

## 7. Linux / macOS setup

```bash
cd finsense-ai
chmod +x run.sh          # only needed if the execute bit was lost when extracting
./run.sh                 # first run: creates .env, builds, migrates, seeds, opens the browser
./run.sh create-admin you@example.com
```

Other commands: `./run.sh stop | status | logs | promote <email> | reset | native | help`. On Linux, your user must be
allowed to talk to Docker (`docker info` must work without `sudo`).

**Apple Silicon:** the API image compiles one dependency that has no Linux/arm64 wheel during the
first build (build tools are included in the build stage). If that build fails on your machine, run
`export DOCKER_DEFAULT_PLATFORM=linux/amd64` and start again (slower, emulated). The arm64 build path
could not be tested in this project's build environment.

## 8. Environment configuration

`run.sh`/`run.bat` create `.env` from [`.env.example`](.env.example) on first start, replacing the
`change-me-...` placeholders with random 48-character passwords. They never overwrite an existing
`.env`. In production mode the API refuses to start while a placeholder password is still set.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `finsense`, `finsense_owner`, *generated* | Owner/bootstrap role (migrations) |
| `APP_DB_PASSWORD` | *generated* | Password of the least-privilege runtime role `finsense_app` |
| `DATABASE_URL`, `MIGRATION_DATABASE_URL` | localhost URLs | Used only by native runs (Docker builds its own URLs) |
| `PUBLIC_APP_URL` | `http://localhost:8080` | Browser address; also the only allowed CORS origin |
| `WEB_PORT`, `WEB_BIND` | `8080`, `127.0.0.1` | Published port; `0.0.0.0` exposes it on your network |
| `COOKIE_SECURE` | unset (= not secure) | Set `true` whenever the site is served over HTTPS |
| `EXPOSE_API_DOCS` | unset (off in Docker, on in native mode) | Interactive docs at `/api/docs` |
| `SEED_DEMO_DATA` | `true` | Load synthetic funds and sample documents on start |
| `RISK_FREE_RATE` | `0.065` | Default annual risk-free rate (users can override) |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` | `none` | Optional language model, see section 12 |
| `AMFI_ENABLED` | `false` | Optional AMFI daily NAV adapter |
| `SMTP_*` | empty | Optional password-reset e-mail |

Other settings (chunk sizes, retrieval weights, evidence-gate thresholds, upload limits, rate limits,
session lifetime) have safe defaults in `backend/app/config.py` and can be overridden with
environment variables of the same name in upper case.

## 9. Database initialisation

**With Docker** everything is automatic:
1. On the first start of the `db` container, `db/init/01-init-roles.sh` enables `pgvector` and
   creates the runtime role `finsense_app` (SELECT/INSERT/UPDATE/DELETE only - it cannot create,
   alter or drop tables).
2. The one-shot `bootstrap` container runs `python -m app.cli bootstrap`: waits for the database,
   applies Alembic migrations with the owner role, then loads demo data and indexes the sample
   documents (both idempotent; disable with `SEED_DEMO_DATA=false`).
3. The `api` container receives only the runtime role's credentials.

### Manual setup without Docker

1. Install PostgreSQL and pgvector:
   * Ubuntu/Debian: `sudo apt install postgresql-16 postgresql-16-pgvector` (PGDG repository).
   * macOS: `brew install postgresql@16 pgvector`.
   * Windows: pgvector must be compiled for your PostgreSQL version (see the
     [pgvector installation notes](https://github.com/pgvector/pgvector#windows)); running only the
     database in Docker is usually easier:
     `docker run -d --name finsense-db -e POSTGRES_PASSWORD=<superuser-password> -p 127.0.0.1:5432:5432 pgvector/pgvector:pg16`
2. Run `./run.sh native` (or `run.bat native`) once; it creates `.env` with random passwords and
   prints the next command.
3. Create the roles and database with the passwords from `.env`, as a PostgreSQL superuser:
   ```bash
   psql -U postgres -h localhost -v owner_password="<POSTGRES_PASSWORD from .env>" \
        -v app_password="<APP_DB_PASSWORD from .env>" -f db/manual-setup.sql
   ```
4. Run `./run.sh native` again. It creates `backend/.venv`, installs dependencies, migrates and
   seeds the database, builds the frontend and serves the app at <http://127.0.0.1:4173>
   (API docs at <http://127.0.0.1:8000/api/docs>). Press Ctrl+C to stop.

Individual commands (from `backend/` with the virtual environment active):

```bash
python -m app.cli migrate          # Alembic upgrade head (uses MIGRATION_DATABASE_URL)
python -m app.cli seed             # synthetic demo data (idempotent)
python -m app.cli ingest-samples   # index the sample documents (idempotent)
python -m app.cli bootstrap        # all three, as Docker does
python -m app.cli create-admin --email you@example.com --name "Your Name"   # add --promote for an existing user
```

## 10. Data import and sample-data generation

**Demo data** (`backend/sample_data/`): 10 synthetic funds (large/mid/small cap, flexi cap growth and
IDCW options, ELSS, index, dynamic bond, liquid, balanced advantage) with daily NAVs from 4 Jan 2021
to 30 Sep 2026 (one fund launches in March 2022 to exercise short-history handling), 7 synthetic
benchmarks, AUM, SIP flows, holdings, an example portfolio, a CSV with deliberate errors, and four
documents (two factsheets six months apart, a scheme information document, an annual review).
Planted events (for example a fund-specific drop on 21 Aug 2025) let tests check anomaly detection.

Regenerate it (deterministic; reproduces the committed files byte for byte):

```bash
cd backend && python sample_data/generate_sample_data.py   # needs reportlab (requirements-dev.txt)
```

**Importing your own data** (Admin → Data imports, or `POST /api/v1/admin/imports/{kind}`). Import in
this order; dates may be `YYYY-MM-DD` or `DD-MM-YYYY`; amounts may contain `₹`, `Rs` and commas.

| Kind | Columns (*optional*) |
|---|---|
| `benchmarks` | `benchmark_code, name, return_basis` (`price` or `total_return`) |
| `benchmark_values` | `benchmark_code, date, value` |
| `funds` | `scheme_code, name, category, asset_class` (equity/debt/hybrid/other), *`amc, plan, option, benchmark_code, launch_date, expense_ratio_pct, risk_label`* |
| `nav` | `scheme_code, date, nav` |
| `aum` | `scheme_code, as_of_date, aum_crore` |
| `sip_flows` | `scheme_code, month, sip_inflow_crore`, *`sip_accounts`* |
| `holdings` | `scheme_code, as_of_date, holding_name, sector, asset_type, weight_pct` |

Each import validates headers and types, rejects bad rows with reasons (shown in the UI), flags
implausible jumps without deleting them, runs in one transaction, and is idempotent (re-importing
the same file changes nothing). Real data is imported with the `csv-import` source and can never be
mixed into a synthetic fund (or vice versa). Portfolios can be imported by users from a CSV with
`scheme_code, weight_pct` (see `sample_data/csv/example_portfolio.csv`).

**Live NAVs (optional):** set `AMFI_ENABLED=true` and restart, then in Admin → *Data status* →
*Live NAVs from AMFI* register scheme codes and press *Sync AMFI NAVs* (or run
`python -m app.cli amfi-sync`). Only the latest NAV is fetched on each sync, so history accumulates
over time; import past NAVs by CSV if you need history. Review AMFI's terms of use first.

## 11. Document ingestion and indexing

* **Upload:** Documents page → *Upload*. Accepted: `.pdf`, `.txt`, `.md` up to 15 MB and 300 pages.
  Optional metadata (fund, document type, as-of date) improves retrieval and conflict detection.
* **Status:** `pending` → `processing` → `indexed`, or `failed` (with the reason) or `needs_ocr`
  (scanned PDF without a text layer). The page refreshes automatically while indexing runs.
* **Inspection:** each document shows its passages (page, section, table flag, warnings such as
  *instruction-like text*), and can be downloaded, re-indexed or deleted by its owner.
* **Sample documents:** indexed automatically by `bootstrap`, or manually with
  `python -m app.cli ingest-samples`.
* **Re-indexing:** every chunk records the embedding model and chunking settings. After changing
  them, run `python -m app.cli reindex` (outdated documents only) or Admin → *Re-index outdated*.

## 12. Embedding and LLM configuration

**Embeddings (default, offline):** WordLlama `l2_supercat`, 256 dimensions, whose weights ship inside
the Python package, so no download or API key is needed. It is a fast static embedding model - good
with exact terms, weaker with paraphrases (see the evaluation). Optional: `EMBEDDING_PROVIDER=fastembed`
(BAAI bge-small, 384 dimensions) downloads the model on first use and needs `EMBEDDING_DIM=384`, a fresh
database migration and re-indexing; this path is **not tested**. `RERANKER=fastembed` (cross-encoder
re-ranking) is likewise optional and **untested**.

**Language model (optional):** without one, answers are verbatim, cited excerpts. To enable summaries:

```dotenv
# Anthropic
LLM_PROVIDER=anthropic
LLM_MODEL=<model id from your Anthropic console>
LLM_API_KEY=<your key>

# Local model with Ollama (OpenAI-compatible); from Docker use host.docker.internal
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=llama3.1
```

Then restart (`./run.sh restart`). Document passages are always sent to the model as untrusted data
inside a delimited block; the API key is never included in prompts, and model output is checked for
leaked instructions, forged citations, unsupported numbers and secrets before it is shown. Note that
the selected passages and the question **are sent to the provider** you configure.

## 13. Starting the application

| Mode | Start | URL |
|---|---|---|
| Docker (recommended) | `run.bat` / `./run.sh` | <http://localhost:8080> |
| Native | `run.bat native` / `./run.sh native` | <http://127.0.0.1:4173> (API on :8000) |
| Development (hot reload) | `uvicorn app.main:app --reload` in `backend/`, `npm run dev` in `frontend/` | <http://localhost:5173> |

There are **no default accounts**. Register in the browser; create administrators explicitly with
`create-admin`. Stop with `run.bat stop` / `./run.sh stop` (data is kept in Docker volumes) or Ctrl+C
in native mode.

## 14. Running the test suites

Backend tests use a real PostgreSQL + pgvector database. They create a throwaway database
`finsense_test` with the owner role (which therefore needs `CREATEDB`, as granted by
`db/manual-setup.sql`), migrate it, seed it, and run the app as the runtime role.

```bash
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
# DATABASE_URL / MIGRATION_DATABASE_URL are read from ../.env
python -m pytest                       # all suites (unit, integration, security, e2e)
python -m pytest tests/unit            # no database needed for most unit tests
python -m pytest --cov=app             # with coverage
ruff check app tests sample_data alembic
pip-audit -r requirements.txt          # dependency vulnerability check

# End-to-end workflow against a running deployment (no database settings needed):
FINSENSE_E2E_BASE_URL=http://localhost:8080 python -m pytest tests/e2e
```

```bash
# Browser smoke test (17 checks of the main workflow, fails on JavaScript errors):
cd scripts && npm install --no-save puppeteer && node ui_smoke.mjs http://localhost:8080
```

```bash
cd frontend
npm ci
npm run lint && npx tsc -b && npm test && npm run build
npm audit
```

Results of the last full verification run are in [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## 15. RAG evaluation and interpretation

`python -m app.cli rag-eval` (or Admin → *Run evaluation*) runs 40 labelled questions
(`backend/sample_data/eval/rag_eval_set.json`: 30 development + 10 hold-out questions covering exact
names and codes, figures, tables, synonyms, multi-section questions, conflicting periods, follow-ups,
no-evidence questions and a permission test with a private document) in three retrieval modes, and
writes `backend/reports/rag_evaluation.{json,md}`.

Latest results (hybrid is the shipped configuration):

| Metric | Hybrid | Vector only | Lexical only |
|---|---|---|---|
| Recall@1 / @3 / @5 | 0.606 / 0.894 / 0.970 | 0.424 / 0.561 / 0.651 | 0.697 / 0.924 / 0.939 |
| Precision@1 | 0.636 | 0.424 | 0.758 |
| MRR | 0.788 | 0.556 | 0.849 |
| nDCG@5 | 0.821 | 0.548 | 0.852 |
| Evidence recall (passages that reached the answer) | 0.894 | 0.227 | 0.864 |
| Abstention accuracy (no-evidence questions declined) | 1.000 | 1.000 | 1.000 |
| False abstention rate | 0.091 | 0.515 | 0.091 |

Also measured for hybrid: conflict detection 1.0, extractive answers containing the expected figure
0.88, invalid citations 0, permission leaks 0. Hold-out questions: Recall@5 1.0, MRR 0.79.

**How to read this honestly:**
* Hybrid retrieval has the best Recall@5 (0.970) and evidence recall, and meets every acceptance
  threshold, but the lexical-only mode ranks the first relevant passage higher (MRR 0.849 vs 0.788).
  With the small offline embedding model, vector similarity adds recall on paraphrased questions but
  also noise at rank 1. A stronger embedding model or a cross-encoder re-ranker would be the next step.
* Misses are concentrated in paraphrased questions ("penalty if I withdraw early" for *exit load*,
  "how quickly could it sell its holdings" for *liquidity*): the static embeddings do not bridge them.
* The corpus is small and synthetic; one question is worth about 3 percentage points. The numbers show
  the pipeline works as designed, not how it would perform on a large real document collection.
  Re-run the evaluation after adding documents.

Full details, settings and the per-question misses: [docs/RAG_EVALUATION.md](docs/RAG_EVALUATION.md).

## 16. Security controls and known limitations

**Controls in place (all covered by automated tests):**
* Argon2id password hashing; password policy; uniform login errors (no account enumeration).
* Opaque random session tokens stored only as SHA-256 hashes; `HttpOnly`, `SameSite=Lax` cookies
  (`Secure` when configured); 12-hour lifetime; logout-everywhere; sessions revoked on password change
  and deactivation.
* CSRF protection (double-submit token header) on every state-changing request.
* Authorisation on every resource: portfolios, documents, passages, conversations and retrieval are
  filtered by owner in the database query itself; other users' resources return 404.
* Least-privilege database role for the running API; parameterised SQL everywhere.
* Rate limits on login (per account+IP and per IP), registration and expensive endpoints.
* Upload hardening: extension allow-list, content sniffing, size/page limits, random storage names,
  path-traversal-proof storage, parsing in a resource-limited subprocess with a timeout.
* Prompt-injection defences: evidence treated as delimited untrusted data, instruction-like text
  flagged, canary-based leak detection, citation validation, secret scrubbing, whitelisted tools.
* Security headers (CSP, frame denial, nosniff, referrer policy, HSTS in production), strict CORS,
  request size limits, uniform error envelope without stack traces, log redaction, audit log.
* Dependency audit: `pip-audit` and `npm audit` reported no known vulnerabilities at release time.

**Known limitations:**
* Rate limiting and background indexing run in-process (single API worker). Multiple workers or
  servers would need a shared store (e.g. Redis) and a job queue.
* No multi-factor authentication and no account lockout beyond rate limiting.
* Documents are stored unencrypted on the server volume; use disk encryption where required.
* Prompt-injection defences reduce but cannot eliminate risk when a language model is enabled.
* ML forecasts did not beat the historical-mean baseline on the demo data and their 80% intervals
  under-cover (see [docs/TEST_REPORT.md](docs/TEST_REPORT.md)); treat them as an educational tool.
* Docker images could not be built in this project's build environment (the container registry was
  not reachable). Compose and Dockerfiles were validated (`docker compose config`, hadolint) and the
  same steps were run natively, but a full `docker compose up` remains to be confirmed on your machine.

More: [docs/SECURITY.md](docs/SECURITY.md).

## 17. Troubleshooting

| Symptom | Fix |
|---|---|
| `Docker is not installed / not running` | Install/start Docker Desktop and wait for "Engine running"; on Linux check `docker info` without sudo. |
| `port is already allocated` / page does not load | Another program uses 8080: set `WEB_PORT=8090` and `PUBLIC_APP_URL=http://localhost:8090` in `.env`, then restart. |
| `set POSTGRES_PASSWORD in .env` | `.env` is missing or incomplete; delete it and run the launcher again (only if you have no data to keep). |
| Bootstrap fails with authentication errors after changing `.env` | Passwords are stored in the database volume on first start. Restore the old `.env`, or `run.sh reset` (deletes data). |
| `The database password is still a placeholder` | Replace the `change-me-...` values in `.env` (or delete `.env` and let the launcher generate it). |
| First build is slow or fails downloading packages | It downloads ~1 GB; retry on a stable connection. Behind a proxy, configure Docker's proxy settings. |
| Apple Silicon build error in the `wheels` stage | `export DOCKER_DEFAULT_PLATFORM=linux/amd64`, then start again. |
| Logged out immediately / 403 on every action | Use the same address as `PUBLIC_APP_URL`; with HTTPS set `COOKIE_SECURE=true`; without HTTPS keep it `false`. |
| `429 Too Many Requests` on login | Rate limit after repeated failures; wait one minute. |
| Document stuck in `needs_ocr` | It is a scanned PDF without text; OCR is not supported. Upload a text-based PDF. |
| Document `failed` | The reason is shown on the document page (corrupt file, too many pages, timeout). |
| Assistant says "insufficient evidence" | The documents you can access do not contain the answer; upload the relevant document or name the fund/scheme code. |
| Native: `Cannot connect to PostgreSQL` | Start PostgreSQL, run `db/manual-setup.sql` with the passwords from `.env`, check host/port in `DATABASE_URL`. |
| Native: `extension "vector" is not available` | Install pgvector for your PostgreSQL version (section 9). |
| Tests: `permission denied to create database` | The owner role needs `CREATEDB` (granted by `db/manual-setup.sql`). |

Logs: `run.sh logs` / `run.bat logs`, or `docker compose logs api bootstrap`.

## 18. API documentation

* OpenAPI schema: [docs/openapi.json](docs/openapi.json) (64 paths under `/api/v1`).
* Interactive docs: set `EXPOSE_API_DOCS=true` (Docker) or use native/development mode, then open
  `/api/docs`.
* Example requests and responses captured from a running server: [docs/api-samples/](docs/api-samples)
  (regenerate with `python scripts/capture_api_samples.py --base-url http://localhost:8080`).
* Conventions: JSON everywhere; errors use `{"error": {"code", "message", "details"}}`; session cookie
  plus `X-CSRF-Token` header for writes; percentages in request bodies are 0-100 (`weight_pct`),
  metric values in responses are fractions (0.12 = 12%) with a `unit` field.

Main groups: `/auth`, `/funds`, `/benchmarks`, `/analytics`, `/dashboard`, `/portfolios`, `/simulate`,
`/ml`, `/documents`, `/assistant`, `/admin`, `/health`, `/system/status`.

## 19. Project directory structure

```
finsense-ai/
├── run.bat / run.sh            launchers (Docker; "native" mode without Docker)
├── docker-compose.yml          db, bootstrap, api, web
├── .env.example                configuration template (placeholders only)
├── HOW_TO_USE.txt              plain-text user guide
├── db/
│   ├── init/01-init-roles.sh   Docker: pgvector + runtime role
│   └── manual-setup.sql        native: roles, database, extension
├── backend/
│   ├── app/
│   │   ├── api/routes/         auth, funds, insights, portfolios, documents, assistant, admin, system
│   │   ├── core/               security, errors, rate limiting, logging, middleware
│   │   ├── services/           analytics, portfolio, simulation, ingestion, ml, rag, assistant
│   │   ├── models.py           SQLAlchemy models
│   │   ├── config.py           settings (environment variables)
│   │   ├── cli.py              management commands
│   │   └── main.py             FastAPI application factory
│   ├── alembic/                migrations
│   ├── sample_data/            synthetic CSVs, documents, generator, RAG evaluation set
│   ├── tests/                  unit, integration, security, e2e
│   ├── reports/                latest RAG evaluation report
│   ├── Dockerfile, requirements.txt, requirements-dev.txt, pyproject.toml
├── frontend/
│   ├── src/                    pages, components, api client, auth, tests
│   ├── deploy/                 nginx site + security headers for the web image
│   ├── Dockerfile, package.json, package-lock.json, vite.config.ts
├── scripts/
│   ├── run_native.py           native launcher used by run.sh/run.bat native
│   ├── capture_api_samples.py  regenerates docs/api-samples
│   └── ui_smoke.mjs            browser smoke test (Puppeteer) against a running deployment
└── docs/
    ├── ARCHITECTURE.md, SECURITY.md, RAG_EVALUATION.md, TEST_REPORT.md
    ├── openapi.json
    └── api-samples/
```

## 20. Responsible deployment

FinSense AI is built for learning, research and decision support. Before exposing it beyond your
own computer:

1. **Serve it over HTTPS** behind a TLS-terminating reverse proxy; set `COOKIE_SECURE=true` and
   `PUBLIC_APP_URL=https://your-domain`. Keep `EXPOSE_API_DOCS=false`.
2. **Use strong, unique secrets** (the launchers generate them) and keep `.env` out of version control
   and backups that others can read. Rotate database passwords if `.env` is ever exposed.
3. **Never present synthetic data as real.** Set `SEED_DEMO_DATA=false` for real deployments and load
   licensed or public data you are allowed to use (check AMFI and document licences).
4. **Keep the disclaimers.** The app gives historical analytics and model estimates, not personalised
   investment advice; in many jurisdictions (for example SEBI rules in India) giving investment advice
   requires registration.
5. **Protect personal data.** Uploaded documents and conversations may contain personal or
   confidential information; restrict access, back up the database and volumes securely, and define a
   retention policy. If you enable an external LLM, the passages and questions are sent to that
   provider - check its data-handling terms or use a local model.
6. **Scale deliberately.** For more than one API worker add a shared rate-limit store and a job queue,
   and monitor logs and the audit trail.
7. **Re-run the test suites and the RAG evaluation** after any change to models, data or settings.

# finsense-ai-financial-intelligence
FinSense AI is a full-stack financial intelligence platform built with React, FastAPI, and PostgreSQL. It combines ML forecasting, risk analysis, portfolio optimization, and LLM-powered RAG for financial document research with cited answers, interactive dashboards, secure authentication, and Docker deployment.
