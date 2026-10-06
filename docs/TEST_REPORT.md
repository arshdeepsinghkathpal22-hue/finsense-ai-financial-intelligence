# FinSense AI - Test and verification report

This report records what was actually run, the results, the problems found on the way, and what could
not be verified. Nothing here is estimated: every number comes from command output captured during
the final verification on 5-6 October 2026.

## 1. Environment

| Item | Version |
|---|---|
| OS | Linux x86_64 (cloud build container) |
| Python | 3.13.16 (main) and 3.12.3 (second full run) |
| Node.js / npm | 22.22.0 / 10.9.4 |
| PostgreSQL | 16.15 with pgvector 0.6.0 |
| Docker | CLI 29.8.2 and Compose 5.5.1 available, but Docker Hub was **not reachable** (HTTP 403 from `registry-1.docker.io`), so no image could be pulled or built |

## 2. How the final verification was done

To test what a user receives rather than the development folder, the release ZIP was built (excluding
`node_modules`, virtual environments, build output, runtime data, caches, logs and `.env`), extracted into
an empty directory, and the documented **native first-run procedure** was followed exactly:

1. `python3.13 scripts/run_native.py` (what `./run.sh native` runs) - it created `.env` with two random
   48-character passwords (file mode 600) and stopped with instructions.
2. A brand-new PostgreSQL 16 cluster (password authentication over TCP) was prepared with
   `psql -U postgres -v owner_password=... -v app_password=... -f db/manual-setup.sql`, using the
   passwords from `.env`.
3. `scripts/run_native.py` again: created `backend/.venv`, installed `requirements.txt`, migrated and
   seeded the database (`app.cli bootstrap`), ran `npm ci` and the production build, and served the app.
   Output ended with `FinSense AI is running at http://127.0.0.1:4173`.
4. All test suites, linters and audits below were run **inside that extracted copy**, against that
   database and running server.
5. Stopping the launcher with SIGTERM stopped both servers (checked that nothing kept listening).

## 3. Results summary

| Check | Command | Result |
|---|---|---|
| Backend unit tests | `python -m pytest tests/unit` | **106 passed** |
| Backend integration tests (real PostgreSQL + pgvector) | `python -m pytest tests/integration` | **54 passed** |
| Security tests | `python -m pytest tests/security` | **44 passed** |
| End-to-end acceptance workflow (in-process) | `python -m pytest tests/e2e` | **1 passed** (12 workflow steps) |
| **Full backend suite, Python 3.13** | `python -m pytest --cov=app` | **205 passed, 0 failed, 0 skipped**, line coverage 86% |
| **Full backend suite, Python 3.12** | same, separate test database | **205 passed, 0 failed, 0 skipped** |
| End-to-end workflow against the live server via the web proxy | `FINSENSE_E2E_BASE_URL=http://127.0.0.1:4173 pytest tests/e2e` | **1 passed** |
| Browser smoke test (headless Chromium, 17 checks) | `node scripts/ui_smoke.mjs http://127.0.0.1:4173` | **17/17 passed, 0 console errors** |
| Backend lint | `ruff check app tests sample_data alembic ../scripts` | **no findings** |
| Python dependency audit | `pip-audit -r requirements.txt` and `-r requirements-dev.txt` | **no known vulnerabilities** |
| Frontend lint | `npm run lint` | **no findings** |
| Frontend type check | `npx tsc -b` | **no errors** |
| Frontend tests | `npm test` (Vitest) | **27 passed** (4 files) |
| Frontend production build | `npm run build` | **succeeded** |
| npm dependency audit | `npm audit` | **0 vulnerabilities** |
| Database migrations | `alembic upgrade head` via `app.cli bootstrap`, and per test session | **succeeded** (fresh cluster and test databases) |
| API start-up and health | `GET /api/v1/health/ready` | `{"status":"ready","database":"ok"}` |
| RAG evaluation | `python -m app.cli rag-eval` | **acceptance passed** (section 6) |
| Shell scripts | `sh -n`, `shellcheck` on `run.sh`, `db/init/01-init-roles.sh` | **no findings** |
| Dockerfiles | `hadolint` (DL3008 apt pinning ignored) | **no findings** |
| Compose file | `docker compose config` | **valid** |
| Docker build / `docker compose up` | - | **NOT RUN** - registry blocked (section 8) |

Per-file test counts (Python 3.13 run): unit - metrics 22, optimiser and simulation 18, RAG units 21,
security and router 14, ML 13, CSV validation 9, LLM adapters 9; integration - documents and assistant 19,
auth and platform 13, analytics API 12, admin and data 9, RAG evaluation 1; security - access control 31,
input and injection 13; e2e 1.

Coverage notes: `rag/extraction.py` shows 27% because PDF parsing runs in a separate subprocess that the
coverage tool does not follow (its behaviour is tested through the API: tables, footers, scanned and
corrupt PDFs). `cli.py` (42%) is exercised mainly by the launcher and bootstrap runs above. The fastembed
code paths in `embeddings.py` and some assistant tool branches are not covered.

## 4. What the main suites check

* **Financial calculations:** returns, CAGR on calendar time, per-period risk-free conversion,
  volatility, Sharpe (including zero variance → n/a) and Sortino, beta/correlation on aligned dates,
  drawdown depth, dates and non-recovery, historical/parametric VaR/CVaR, rolling metrics, concentration,
  portfolio volatility, diversification and buy-and-hold drift - checked against known values and
  closed-form formulas, plus rejection of too few observations, invalid prices, duplicate dates and
  invalid confidence levels; the API risk report is cross-checked against a direct calculation.
* **Optimiser and simulations:** analytic minimum-variance and tangency portfolios, risk aversion vs
  volatility, bounds respected, infeasible limits explained, shrinkage on a singular covariance, non-PSD
  input rejected, frontier monotonic; SIP vs annuity-due formula, lump sum, Monte Carlo reproducibility,
  beta-weighted market shock, stress with perfect correlation.
* **ML:** no look-ahead in features, purge gaps, forward targets, baseline comparison, interval coverage,
  SHAP output, model registry reuse, planted anomaly found.
* **Ingestion:** header/type validation, date formats, duplicates, negative/missing values, unknown
  schemes, outlier warnings, idempotent re-import, synthetic/real separation.
* **RAG:** page-aware extraction, tables, footer removal, scanned/corrupt PDF handling, chunk sizes and
  overlap, citation validation, unsupported-number warnings, prompt wrapping, secret scrubbing, access
  filters, conflict detection, follow-ups, abstention, LLM adapters (mocked HTTP), deterministic fake LLM.
* **Security:** see docs/SECURITY.md for the mapping of each control to its tests.
* **End-to-end (12 required steps):** health → register/login → seeded data → risk metrics → create and
  optimise a portfolio and apply weights → simulations and an ML forecast → upload a document and wait for
  indexing → question answered from that document with a cited source and page → analytics question →
  unsupported question declined → a second user is denied the portfolio, the document and the answer →
  clean-up and logout.

## 5. Browser smoke test (17 checks)

Anonymous redirect to sign-in; weak password rejected in the form; registration and sign-in; dashboard
KPIs with the synthetic-data label; fund list; "not covered" for a short history; fund comparison;
correlation matrix; optimiser with efficient frontier; applying optimised weights; SIP projection; ML
forecast with backtest; document upload through the UI reaching "indexed"; assistant answer citing the
uploaded document; assistant declining an unanswerable question; settings/system status; administration
hidden from a regular user. No JavaScript errors were logged.

Earlier in development the production configuration was also checked in a browser behind the project's
nginx configuration (`frontend/deploy/nginx.conf`, API in production mode): all 12 routes loaded with
**no Content-Security-Policy violations**, and the admin AMFI panel registered and synced a scheme from a
local copy of the AMFI file format.

## 6. RAG evaluation (final configuration)

| Metric | Hybrid | Vector only | Lexical only |
|---|---|---|---|
| Recall@5 | 0.970 | 0.651 | 0.939 |
| MRR | 0.788 | 0.556 | 0.849 |
| nDCG@5 | 0.821 | 0.548 | 0.852 |
| Evidence recall | 0.894 | 0.227 | 0.864 |
| Abstention accuracy | 1.000 | 1.000 | 1.000 |

Hold-out split (hybrid): Recall@5 1.0, MRR 0.792. Permission leaks 0, invalid citations 0, conflicting
periods detected 2/2. Lexical-only ranks better at position 1 than hybrid; see docs/RAG_EVALUATION.md.
The same result was reproduced on three independently built databases.

## 7. ML evaluation on the demo data (all 10 funds)

Run with the service code over every fund, both learned models and all horizons, scoring the held-out
test period:

| Model | Horizon (trading days) | Beats historical-mean baseline (MAE) | Beats zero-return baseline | Mean 80%-interval coverage |
|---|---|---|---|---|
| Ridge | 5 / 21 / 63 | 1 / 0 / 1 of 10 funds | 2 / 2 / 2 of 10 | 0.70 / 0.55 / 0.43 |
| Random forest | 5 / 21 / 63 | 0 / 0 / 0 of 10 funds | 2 / 2 / 2 of 10 | 0.69 / 0.58 / 0.64 |

The models essentially never beat the naive baselines, and the 80% intervals under-cover (the test period
contains a regime change). The app reports both facts to the user; forecasting is presented as an
educational feature, not a trading signal. The LSTM option is not implemented and is shown as unavailable.

## 8. Not verified (and why)

| Item | Status |
|---|---|
| Building images and `docker compose up` | **Not run.** Docker Hub returned HTTP 403 in the build environment. Verified instead: `docker compose config`, hadolint, the database init script on a fresh PostgreSQL cluster created like the official image (pgvector enabled, runtime role DML-only, `CREATE TABLE` denied), the bootstrap → API → nginx chain run natively with the same production settings, and the image's "wheels first, then offline install" step on Linux x86_64 / Python 3.13 (all 71 packages available as wheels). |
| Docker on Apple Silicon (arm64) | Not tested. One dependency (wordllama) has no Linux/arm64 wheel; the image compiles it in a build stage. Fallback documented (`DOCKER_DEFAULT_PLATFORM=linux/amd64`). |
| `run.bat` | Not executed (no Windows machine). Reviewed manually; uses only cmd built-ins, `docker`, and PowerShell for password generation and health polling. |
| Native mode on Windows/macOS | Not tested; the dependency lock resolves for Windows x64 and Apple Silicon. Intel macOS is unsupported natively. |
| Real LLM providers (Anthropic, OpenAI-compatible/Ollama) | Not called - no API key or local model available. Adapters tested against mocked HTTP; the assistant against a deterministic fake model, including a "compromised" model that tries to leak secrets. |
| Optional fastembed embeddings / re-ranker | Not tested (model download not possible). Off by default. |
| SMTP password-reset e-mail | Not sent. Token flow tested with a mocked sender; without SMTP the endpoint returns a clear 503. |
| Live AMFI website | Not contacted. Parser and sync tested with mocked HTTP and a locally served copy of the file format. |

## 9. Problems found during verification (all fixed and re-tested)

1. Trailing returns for a fund younger than the period were computed since launch and labelled "5y" -
   now shown as "not covered"; risk reports state when history is shorter than the requested period.
2. Creating a portfolio with a duplicate name returned HTTP 500 - now 409 with a clear message.
3. `pip-audit` reported 12 advisories in Starlette 0.48 - upgraded to FastAPI 0.142.2 / Starlette 1.7.0;
   the full suite was re-run on both Python versions.
4. A developer's `.env` could change test behaviour (and the template disabled API docs in native mode) -
   tests now read only database settings from `.env`; optional settings are commented out in the template.
5. The native launcher left the Vite server running after being stopped - Vite is now started directly.
6. Retrieval tie-breaking depended on random passage ids, so evaluation scores varied between database
   rebuilds (Recall@5 0.92-0.97) - ties are now broken by content; the parameter grid was re-run.
7. The Docker database init script used `set -u`, unsafe if the postgres entrypoint sources it - fixed and
   made executable; verified on a fresh cluster.
8. The README described AMFI controls the admin page did not have - the panel was added and tested.
9. `create-admin --promote` asked for a password it did not need - fixed, with a test.

Earlier development rounds (before this final verification) fixed, among others: first-run RAG evaluation
failing its MRR and abstention thresholds (retrieval and evidence-gate redesign, documented in
RAG_EVALUATION.md), conversation records lost on a tool error, extractive answers quoting the question,
an AUM series discontinuity in the synthetic data, directional accuracy reported for a model that never
predicts a direction, and `CORS_ORIGINS` parsing.

## 10. Reproducing these results

```bash
# backend (from backend/, with .env pointing at a PostgreSQL + pgvector server)
pip install -r requirements-dev.txt
python -m pytest --cov=app
ruff check app tests sample_data alembic ../scripts
pip-audit -r requirements.txt
python -m app.cli rag-eval

# frontend (from frontend/)
npm ci && npm run lint && npx tsc -b && npm test && npm run build && npm audit

# against a running deployment
FINSENSE_E2E_BASE_URL=http://localhost:8080 python -m pytest tests/e2e      # from backend/
cd scripts && npm install --no-save puppeteer && node ui_smoke.mjs http://localhost:8080
```
