# FinSense AI - Security

This document lists the threats considered, the controls implemented, where each control is tested,
and the risks that remain. For deployment guidance see README section 20.

## 1. Assets and threats

| Asset | Threats considered |
|---|---|
| User accounts and sessions | credential stuffing, password guessing, session theft, CSRF, account enumeration |
| Private portfolios, documents, conversations | access by other users (IDOR), leakage through search or the assistant |
| Database and server | SQL injection, privilege escalation, path traversal, malicious files, resource exhaustion |
| Secrets (DB passwords, LLM key, SMTP password) | exposure in responses, logs, prompts, model output, source code or the ZIP |
| Users' decisions | misleading figures, synthetic data mistaken for real data, prompt-injected answers |

## 2. Controls and their tests

| Control | Implementation | Tests |
|---|---|---|
| Password storage | Argon2id (`argon2-cffi`), policy: 10-128 chars, mixed case, digit; a dummy hash check runs for unknown users | `tests/unit/test_security_and_router.py`, `tests/security/test_access_control.py::test_sessions_and_passwords_are_stored_hashed` |
| Uniform login errors | same status and message for unknown user and wrong password | `tests/integration/test_auth_and_platform.py::test_wrong_password_is_rejected_without_revealing_accounts` |
| Sessions | 256-bit random token, only SHA-256 stored; `HttpOnly`, `SameSite=Lax`, `Secure` when configured; 12 h expiry; revoked on logout, logout-all, password change; rejected for deactivated users | `test_auth_and_platform.py` (cookie flags, change password, logout-all), `test_admin_and_data.py::test_admin_can_deactivate_user`, `test_access_control.py::test_forged_session_cookie_is_rejected` |
| CSRF | double-submit token: readable `fs_csrf` cookie must match `X-CSRF-Token` on every non-GET request | `test_access_control.py::test_state_changing_requests_need_the_csrf_token` |
| Authentication required | every endpoint except health, register, login, password reset | `test_access_control.py::test_protected_endpoints_require_a_session` (14 endpoints) |
| Role checks | admin routes require `role = admin`; users cannot change their own role | `test_access_control.py::test_admin_endpoints_reject_regular_users`, `::test_users_cannot_promote_themselves` |
| Object-level authorisation | owner filters in every query; documents filtered inside the retrieval SQL; non-owners get 404 | `test_access_control.py` (portfolios, documents, passages, files, re-index, delete, conversations, assistant, search with explicit document ids, admin diagnostics) |
| Assistant tool isolation | tools run with the caller's permissions; LLM-proposed tools validated against a closed whitelist and the funds named in the question | `test_access_control.py::test_assistant_tools_cannot_reach_another_users_portfolio`, `test_security_and_router.py::test_llm_planner_output_is_validated` |
| Least-privilege database role | API connects as `finsense_app` (DML only); migrations use the owner role in a separate container | `test_auth_and_platform.py::test_migrations_created_schema_and_runtime_role_is_least_privilege` |
| SQL injection | SQLAlchemy parameters everywhere; full-text lexemes filtered by a strict pattern | `tests/security/test_input_and_injection.py::test_sql_injection_attempts_are_inert` |
| Upload validation | extension allow-list, PDF signature and EOF check, UTF-8 check, binary detection, 15 MB limit, duplicate detection | `test_documents_and_assistant.py::test_malformed_uploads_are_rejected`, `::test_oversized_upload_is_rejected`, `::test_duplicate_upload_is_rejected` |
| Safe storage | random storage keys, strict key pattern, resolved-path check, files created `0600`, original name used for display only | `test_input_and_injection.py::test_upload_filenames_cannot_traverse_paths` |
| Parser isolation | extraction in a subprocess with wall-clock timeout, CPU and address-space limits (POSIX), page limit | `tests/unit/test_rag_units.py` (corrupt and scanned PDFs), `test_documents_and_assistant.py::test_scanned_and_corrupt_pdfs_get_clear_statuses` |
| Request limits | body-size middleware, bounded numeric inputs (e.g. Monte Carlo paths), page-size limits | `test_input_and_injection.py::test_declared_oversized_bodies_are_refused_early`, `::test_input_limits_are_enforced` |
| Rate limiting | login 5/min per IP+email and 20/min per IP, registration 10/h per IP, expensive endpoints 20/min per user; `429` with `Retry-After` | `test_input_and_injection.py::test_login_is_rate_limited`, unit test of the limiter |
| Prompt injection | evidence delimited and escaped as untrusted data; instruction-like passages flagged; per-request canary; forged citations removed; unsupported numbers flagged; secrets scrubbed from output; LLM never receives secrets | `test_input_and_injection.py::test_prompt_injection_in_a_document_cannot_leak_secrets`, `test_rag_units.py` (prompt wrapping, leak and secret scrubbing), `test_documents_and_assistant.py::test_assistant_with_language_model_validates_citations` |
| Secrets handling | settings use `SecretStr`; secrets redacted from logs; not returned by any endpoint; `.env` never packaged; placeholder passwords refused in production | `test_input_and_injection.py::test_secrets_never_appear_in_responses`, `::test_logs_redact_secrets`, `test_security_and_router.py::test_configuration_rejects_unsafe_values` |
| Browser hardening | CSP (`default-src 'none'` for the API; `'self'` for the SPA), `X-Frame-Options: DENY`, `nosniff`, referrer policy, COOP, HSTS in production, `Cache-Control: no-store` on API responses | `test_input_and_injection.py::test_security_headers_are_set`; SPA headers checked against the nginx config with a headless browser (no CSP violations) |
| CORS | explicit origin list (wildcard rejected at start-up), credentials only for listed origins | `test_input_and_injection.py::test_cors_allows_only_configured_origins` |
| Error handling | uniform `{"error": {...}}` envelope; unexpected errors return a generic 500 and are logged server-side | `test_input_and_injection.py::test_malformed_json_returns_the_error_envelope` |
| Audit trail | logins, failed logins (email only, never the password), registrations, password changes, uploads, deletions, imports, admin actions | `test_access_control.py::test_failed_logins_are_audited_without_the_password`, `test_admin_and_data.py::test_admin_overview_endpoints` |
| Data integrity | synthetic and real observations can never be mixed in one series; imports are transactional | `test_admin_and_data.py::test_real_and_synthetic_data_are_never_mixed` |
| Containers | non-root users (API uid 10001, unprivileged nginx), database port not published, web port bound to 127.0.0.1 by default | configuration review; `docker compose config` validated (images not built here, see TEST_REPORT) |
| Dependencies | pinned lockfiles; `pip-audit` and `npm audit` | both reported 0 known vulnerabilities (see TEST_REPORT) |

## 3. Residual risks and limitations

* **Single-process state.** Rate limits and indexing jobs live in the API process. Running several
  workers or replicas weakens rate limiting and needs a shared store and job queue.
* **No MFA or lockout.** Brute force is slowed by rate limits only.
* **Prompt injection** cannot be fully prevented when an LLM is enabled. The defences limit what an
  injected instruction can achieve (no tools beyond the whitelist, no secrets in the prompt, output
  checks), but a model could still be persuaded to phrase cited facts misleadingly.
* **LLM data sharing.** With an external provider configured, the question and selected passages are
  sent to that provider.
* **Storage encryption.** Uploaded files and the database are not encrypted by the application; use
  encrypted disks/volumes where required.
* **Malicious PDFs.** Parsing is isolated and limited, but relies on pdfplumber/pdfminer; keep
  dependencies updated.
* **Not verified here:** real SMTP delivery, live AMFI downloads, real LLM providers, the optional
  fastembed models, and building/running the Docker images (registry access was blocked in the build
  environment).

## 4. Operating securely

* Serve over HTTPS and set `COOKIE_SECURE=true`; keep `EXPOSE_API_DOCS=false` on public servers.
* Keep `.env` private (the launchers create it with mode 600 on Linux/macOS); rotate passwords if exposed.
* Review the audit log (Admin → Audit log) and deactivate unused accounts.
* Re-run `pip-audit -r backend/requirements.txt` and `npm audit` regularly and rebuild images.

## 5. Reporting a problem

If you find a vulnerability in your deployment of this project, do not post exploit details publicly;
contact the maintainer of your deployment privately with steps to reproduce.
