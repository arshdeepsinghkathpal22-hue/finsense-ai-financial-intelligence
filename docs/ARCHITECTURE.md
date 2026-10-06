# FinSense AI - Architecture

This document describes how the system is put together, why the main design decisions were made,
and where each responsibility lives in the code. Start with the README for setup.

## 1. System overview

```
                 ┌──────────────────────────────── web (nginx, unprivileged) ─────────────┐
 Browser ──────▶ │  /            React single-page app (static build, CSP, no-cache HTML)   │
 localhost:8080  │  /assets/*    fingerprinted JS/CSS (long cache)                          │
                 │  /api/*       reverse proxy ──▶ api:8000 (X-Forwarded-For overwritten)   │
                 └───────────────────────────────────────┬────────────────────────────────┘
                                                         ▼
                 ┌────────────────────────────── api (FastAPI + Uvicorn, 1 worker) ────────┐
                 │ middleware: security headers, body-size limit, CORS (explicit origins) │
                 │ routes /api/v1: auth, funds, analytics, dashboard, portfolios,          │
                 │   simulate, ml, documents, assistant, admin, health, system             │
                 │ services: analytics · portfolio · simulation · ingestion · ml · rag ·   │
                 │   assistant (orchestration) · audit · auth                              │
                 │ background tasks: document indexing (in-process)                        │
                 │ subprocess: PDF/text extraction with CPU/memory/time limits             │
                 └───────────────┬───────────────────────────────┬────────────────────────┘
                                 │ runtime role finsense_app     │ volume app-data
                                 ▼ (DML only)                    ▼ /app/var: uploads, models, cache
                 ┌──────────── db (PostgreSQL 16 + pgvector) ───────────┐
                 │ relational data · vector(256) HNSW · tsvector GIN    │
                 └──────────────────────────────────────────────────────┘
 bootstrap (one-shot, owner role): wait for db → alembic upgrade → seed synthetic data → index samples
```

The browser only ever talks to one origin. Session cookies are therefore first-party and CORS is not
needed in the Docker deployment (the API still enforces an explicit allow-list for development).

## 2. Backend layout

| Package | Responsibility |
|---|---|
| `app/main.py` | Application factory: middleware, error handlers, routers, OpenAPI settings |
| `app/config.py` | Typed settings from environment / `.env`; consistency checks (no `*` CORS, LLM settings complete, no placeholder passwords in production) |
| `app/db.py`, `app/models.py` | Engine/session factory; SQLAlchemy 2 models |
| `app/api/deps.py` | Current session/user, CSRF check, admin guard, client IP, rate-limit dependencies |
| `app/api/routes/*` | Thin HTTP layer: validation (Pydantic), authorisation, calls into services |
| `app/core/` | Password hashing and tokens, error types and envelope, rate limiter, log redaction, middleware |
| `app/services/analytics` | Pure metric functions (`metrics.py`), series loading/alignment (`series.py`), report assembly (`reports.py`) |
| `app/services/portfolio` | Optimiser (`optimizer.py`) and portfolio reports/optimisation over stored data (`analysis.py`) |
| `app/services/simulation.py` | SIP, Monte Carlo, shock, stress and allocation-change scenarios |
| `app/services/ingestion` | CSV parsing/validation, transactional idempotent import, AMFI adapter |
| `app/services/ml` | Features, forecasting models, evaluation, SHAP, registry, anomaly detection |
| `app/services/rag` | Extraction, chunking, embeddings, indexing, query processing, retrieval, LLM adapters, generation, citations, evaluation |
| `app/services/assistant` | Tool registry, rule-based and LLM planner, orchestration of tools + retrieval + generation |
| `app/cli.py` | Management commands (bootstrap, migrate, seed, ingest-samples, create-admin, reindex, rag-eval, amfi-sync) |

Pure calculation modules (`metrics`, `optimizer`, `features`, `chunking`, `citations`, `query`) have no
database or HTTP dependencies, which keeps them independently unit-testable.

## 3. Data model

| Table | Purpose / notable constraints |
|---|---|
| `users` | email (unique, case-normalised), Argon2id hash, role `user`/`admin`, active flag, preferences JSON |
| `user_sessions` | SHA-256 of session token and CSRF token, expiry, last seen, IP, user agent |
| `password_reset_tokens` | hashed single-use tokens with expiry |
| `audit_events` | security-relevant events (login, failures, password changes, uploads, admin actions) |
| `data_sources` | provenance: `synthetic-demo`, `csv-import`, `amfi`; synthetic flag |
| `benchmarks`, `benchmark_observations` | benchmark metadata (price or total-return basis) and levels |
| `funds` | scheme code (unique), category, asset class, option, return basis (`nav_growth` or `nav_price` for IDCW), synthetic flag, source |
| `nav_observations` | primary key (fund, date); NAV > 0 enforced on import |
| `fund_aum`, `fund_sip_flows`, `fund_holdings` | AUM history, monthly SIP flows, holdings disclosures (unique per fund/date/name) |
| `import_batches` | every CSV import: counts, warnings, rejected rows with reasons, content hash |
| `portfolios`, `portfolio_assets` | user-owned (unique name per user); weights stored as fractions |
| `documents` | owner (NULL = shared library), visibility, random storage key, SHA-256 (unique per owner), status, metadata, index configuration |
| `document_chunks` | text, page range, section, table flag, warning flags, `vector(256)` embedding (HNSW, cosine), generated weighted `tsvector` (GIN) |
| `conversations`, `messages` | assistant history with sources, calculations and warnings |
| `ml_models` | trained model metadata, metrics and data fingerprint (artifact files live on the volume) |
| `rag_evaluation_runs` | stored evaluation results |

The `tsvector` is generated by PostgreSQL from the section heading (weight A), passage text (B) and a
context label - document title, fund name and scheme code (C) - so a passage that never repeats the
fund name can still be found by it.

Migrations: `backend/alembic/versions/0001_initial_schema.py`. pgvector must be created by a superuser
(Docker init script or `db/manual-setup.sql`); the migration checks for it and fails with a clear
message otherwise.

## 4. Financial calculation conventions

* Returns are simple daily returns of NAV (or benchmark level) series. Growth-option NAVs are total
  return; IDCW-option NAVs are price-only and labelled as such (dividends are never assumed to be zero).
* Annualisation: 252 trading days; CAGR uses calendar time (365.25 days).
* Sharpe and Sortino subtract a per-period risk-free rate derived geometrically from the annual rate
  (default 6.5%, user-configurable); Sortino uses downside deviation below that target.
* Beta, correlation, alpha, tracking error and information ratio use only dates present in both the
  fund and benchmark series.
* Maximum drawdown is computed on the cumulative wealth series, with peak, trough and recovery dates.
* Historical VaR/CVaR use the empirical distribution with NumPy's linear-interpolation quantile;
  parametric VaR/CVaR assume normal returns. Both report the confidence level and method.
* Every report includes an `assumptions` block, a freshness block (latest observation, staleness), and
  per-metric `unavailable_reason` when a metric cannot be computed (too few observations, zero variance,
  no benchmark, history shorter than the requested period).

## 5. Portfolio optimisation

`optimizer.py` estimates annualised mean returns and the covariance matrix from aligned daily returns
over the look-back window. If the covariance matrix is ill-conditioned it switches to Ledoit-Wolf
shrinkage and reports that. Objectives: mean-variance utility `w'μ - (λ/2) w'Σw` (risk profiles map to
λ = 8 / 4 / 1.5), minimum variance, maximum Sharpe. Constraints: long-only, weights sum to one,
per-asset minimum/maximum. Feasibility (e.g. `n × max_weight < 1`) is checked before solving and returned
as a 422 with an explanation. SLSQP starts from equal weights (clipped to the bounds); the result is
compared with the current and equal-weight portfolios, and an efficient frontier is traced for the chart.

## 6. Machine learning pipeline

1. **Features** (`features.py`): trailing log returns (1, 5, 21, 63 days), 1- and 3-month volatility and
   their ratio, distance below the 1-year high, benchmark 5- and 21-day returns and the 1-month return
   relative to the benchmark - each computed only from data up to the prediction date (252-day warm-up).
   A unit test checks that adding future data never changes past feature values.
2. **Target:** forward log return over the horizon (5, 21 or 63 trading days).
3. **Split:** chronological train / validation / test with a purge gap equal to the horizon between
   them, so overlapping targets cannot leak across the boundary.
4. **Models:** historical mean (baseline), ridge regression, random forest; hyper-parameters chosen on
   validation, then refit on train+validation and evaluated once on the test period.
5. **Evaluation:** MAE, RMSE, directional accuracy (only for models that commit to a direction), 80%
   interval coverage, compared with the historical-mean and zero-return baselines. `beats_baseline` is
   reported honestly.
6. **Explanations:** SHAP TreeExplainer / LinearExplainer for global importance and the latest forecast.
7. **Registry:** models are saved with joblib together with a fingerprint of the input data; a request
   with unchanged data reuses the stored model.
8. **Anomalies:** Isolation Forest over the daily return, its rolling z-score, the short/long volatility
   ratio and the excess return over the benchmark; each flagged day is explained with robust (median/MAD)
   z-scores of those features.

## 7. RAG pipeline

```
upload ─▶ validate (extension, signature, size, duplicate) ─▶ store under random key
       ─▶ background job ─▶ extraction subprocess (pages, tables, headings; footer removal; OCR detection)
       ─▶ structure-aware chunks (~180 words, 30 overlap; tables kept with header rows)
       ─▶ embeddings (WordLlama 256-d) ─▶ document_chunks (vector + generated tsvector)

question ─▶ normalise, follow-up expansion, fund-name resolution, period detection
         ─▶ vector top-30  ┐ both SQL queries include the access filter
         ─▶ lexical top-90 ┘ (OR-tsquery, re-ranked by IDF-weighted term coverage → top-30)
         ─▶ weighted RRF (k = 60, vector 0.3, lexical 1.0) ─▶ boosts (named fund ×1.15, latest period)
         ─▶ evidence gate ─▶ de-duplicate, ≤ 4 passages per document, ≤ 1,600 words, top 6
         ─▶ conflicts (same fund, different as-of dates)
         ─▶ answer: LLM with delimited evidence, or verbatim extractive excerpts
         ─▶ citation validation, numeric grounding check, leak/secret checks ─▶ response + sources
```

**Evidence gate.** A question is answerable only if the best eligible passage covers at least half of
the question's informative (IDF-weighted) terms, or its embedding similarity is at least 0.6. Other
passages are admitted when they are close to the best one on the same signal. When the question names a
fund, passages from documents about other funds are not eligible. Thresholds were tuned on the
development questions only; hold-out questions measure generalisation (see RAG_EVALUATION.md).

**Generation guards.** The system prompt contains a per-request canary; output containing it is withheld.
Evidence is wrapped in an `<evidence>` block with closing tags escaped so a document cannot break out.
Only `[S#]` and `[T#]` markers that refer to supplied sources survive. Numbers in the answer that do not
appear in the evidence or calculations produce a warning. Configured secrets are scrubbed from any output.

**Orchestration.** The assistant resolves fund and portfolio names, then plans tool calls: a rule-based
router by default, or an LLM planner whose JSON output is validated against the closed tool registry and
the funds actually named in the question (anything else falls back to the rules). Tools run with the
caller's permissions; their results become `[T#]` calculations.

## 8. Security architecture

* **Authentication:** server-side sessions; the cookie holds a random 256-bit token whose SHA-256 is
  stored. A second random token (readable cookie) must be echoed in `X-CSRF-Token` on writes.
* **Authorisation:** ownership checks in every route; document access is part of the SQL used for
  retrieval; non-owners receive 404 so resource existence is not revealed.
* **Least privilege:** migrations use the owner role; the API runs as `finsense_app` (DML only); the API
  container never receives owner credentials; containers run as non-root users.
* **Input handling:** Pydantic validation with bounds on every numeric input, body size limits, upload
  sniffing and sandboxed parsing, parameterised SQL (tsquery lexemes are additionally filtered).
* **Abuse controls:** rate limits on authentication and expensive endpoints, audit log.
* **Transport and browser:** strict CSP for the SPA and API, frame denial, no-sniff, HSTS in production,
  `Secure` cookies when served over HTTPS.

## 9. Frontend

React 19 with React Router (lazy-loaded routes) and TanStack Query for server state. `src/api/client.ts`
is the only place that performs HTTP requests: it adds the CSRF header, normalises the error envelope and
redirects to login on 401. Pages: landing, auth (login, register, forgot/reset password), dashboard, fund
explorer/detail/compare, risk analytics, portfolios (builder and detail with optimiser), research
assistant, documents (library and detail), ML, what-if, settings, admin. Charts use Recharts; every chart
has a caption that states the data window and basis. Synthetic data is badged wherever it appears.

## 10. Key decisions and trade-offs

| Decision | Reason | Trade-off |
|---|---|---|
| Hybrid retrieval with RRF instead of score blending | Cosine and `ts_rank` live on different scales; RRF needs no calibration | Rank-only fusion ignores score gaps |
| Offline WordLlama embeddings by default | Runs without downloads, GPUs or keys; fast | Weak on paraphrases; lexical retrieval does most of the work |
| Extractive answers when no LLM is configured | Never invents text; works offline | Less fluent; long passages |
| Evidence gate before generation | Prevents answering from loosely related passages | Some paraphrased questions are declined (false abstention 9%) |
| Server-side sessions over JWT | Immediate revocation, nothing sensitive in the browser | Requires a database lookup per request |
| In-process background tasks and rate limiter | No extra services to install | Single API worker; not horizontally scalable as-is |
| Separate bootstrap container | Owner credentials never reach the long-running API | One more service in Compose |
