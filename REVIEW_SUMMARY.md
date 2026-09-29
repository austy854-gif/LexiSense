# LexiSense — Code Review & Fix Report

**Repository:** https://github.com/austy854-gif/LexiSense
**Reviewed revision:** `59476d9` (main)
**Review output:** `LexiSense_v2/` (versioned copy — original left untouched)
**Scope:** Full-stack review of the FastAPI backend, React frontend, and deployment/config layer.

---

## 1. What was reviewed

| Area | Files |
|------|-------|
| Backend entrypoint & infra | `backend/server.py`, `backend/utils/auth.py`, `backend/utils/logging_config.py`, `backend/utils/migrations.py` |
| Backend routes | `auth.py`, `contracts.py`, `team.py`, `billing.py`, `workflow.py`, `dashboard.py`, `alerts.py`, `analytics.py`, `export.py`, `templates.py`, `audit.py`, `notifications.py` |
| Backend services | `ai_service.py`, `storage_service.py`, `pdf_service.py`, `pdf_export_service.py`, `email_service.py`, `audit_service.py` |
| Backend models | `contract.py`, `user.py`, `billing.py`, `invitation.py`, `template.py`, `contract_version.py`, `alerts.py`, `audit.py`, `notification.py`, `organization.py` |
| Frontend | `src/api.js`, `src/App.js`, `src/contexts/AuthContext.js`, `src/components/Layout.js`, `src/pages/*` |
| Config / deploy | `docker-compose.yml`, `docker-compose.prod.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`, `.github/workflows/ci-cd.yml`, `.gitignore`, `start-dev.sh`, `seed_database.py` |

---

## 2. Bugs found and fixed

### 2.1 `backend/seed_database.py` — broken import (crash on run) — **High**
`from models.template import Template, TemplateCreate` — `models/template.py` defines `ContractTemplate`, not `Template`. The seed script raised `ImportError` on every run, so a fresh deployment could never seed its default templates.
**Fix:** import `ContractTemplate` and instantiate it. Also persist `isDefault: True` explicitly, because `ContractTemplate` uses `extra="ignore"` and silently dropped that key — which meant the script's own "already seeded?" guard (`count_documents({"isDefault": True})`) never matched and would re-seed on every run.

### 2.2 `frontend/src/api.js` — undefined base URL — **High**
`const API_URL = process.env.REACT_APP_BACKEND_URL + '/api/v1'` produced the literal string `"undefined/api/v1"` when the env var was unset, so every request failed with a confusing 404 instead of a clear configuration error.
**Fix:** default to `''`, strip trailing slashes, and log an explicit console error when the variable is missing.

### 2.3 `docker-compose.prod.yml` — healthcheck hit a non-existent path — **High**
The backend healthcheck called `http://localhost:8000/api/health`, but the app only serves `/api/v1/health` (the un-versioned path correctly returns 404). The container would be marked unhealthy forever and, because the frontend `depends_on: backend: condition: service_healthy`, the whole stack would never start.
**Fix:** corrected to `/api/v1/health`.

### 2.4 `docker-compose.yml` / `docker-compose.prod.yml` — `env_file` pointed at a missing file — **High**
All backend services used `env_file: - .env`, but the documented setup places the file at `backend/.env` (`cp ../.env.example .env` from inside `backend/`). Compose fails hard when an `env_file` does not exist, so `docker compose up` aborted.
**Fix:** pointed the backend/celery services at `backend/.env` and the prod backend at `backend/.env`.

### 2.5 `.gitignore` — `.env.example` templates were silently ignored — **High**
The root `.gitignore` contained `.env.*`, which also matches `.env.example`. The security fix documented in `AUDIT_REPORT.md` (SEC-01/02/03) was therefore ineffective: the template files could never be committed, so new developers had nothing to copy. The file also contained duplicated blocks and corrupted lines (a stray `android-sdk/ frontend/node_modules/...` merge and two orphan `-e ` lines).
**Fix:** removed the corrupted/duplicated lines and added explicit negations (`!.env.example`, `!**/.env.example`) so the templates are trackable while real `.env` files stay ignored.

### 2.6 Missing `.env.example` files — **High**
`AUDIT_REPORT.md` claims root, `backend/`, and `frontend/` `.env.example` files were created, but none existed in the repository.
**Fix:** created all three, documenting every variable the code actually reads (`JWT_SECRET`, `CORS_ORIGINS`, `EMERGENT_LLM_KEY`, `RESEND_*`, `APP_URL`, `AWS_*`, `STRIPE_*`, `LOG_*`, `SENTRY_DSN`, `CELERY_*`).

### 2.7 `backend/routes/contracts.py` — regex injection in search filters — **Medium**
`search` and `counterparty` were interpolated straight into MongoDB `$regex` queries, so user input was interpreted as a regular expression (regex injection / ReDoS).
**Fix:** added `_escape_regex()` (`re.escape`) and applied it to both filters.

### 2.8 `backend/routes/contracts.py` — `expiring_within` clobbered the status filter — **Medium**
When `expiring_within` was supplied, the code unconditionally overwrote `query["status"]`, discarding any explicit `status_filter` the caller had passed.
**Fix:** only apply the default `{"$ne": "expired"}` when no status filter is present.

### 2.9 `backend/routes/contracts.py` — PATCH used query parameters and had no RBAC — **Medium**
`PATCH /contracts/{id}` took its fields as bare query parameters (leaking update payloads into URLs/logs) and performed no role check, so a `viewer` could modify any contract in the organization.
**Fix:** introduced a validated `ContractUpdate` body model, added viewer rejection and an uploader/admin/manager ownership check, and validated the `status` value against the workflow states.

### 2.10 `backend/routes/team.py` — password sent as a query parameter — **Medium**
`POST /team/accept-invite` accepted `password` as a query parameter, exposing it in access logs, browser history, and proxy logs.
**Fix:** added an `AcceptInvitationRequest` body model (with `min_length=8` on the password) and updated the frontend `acceptInvite` call to post a JSON body. Also hardened the expiry comparison to be timezone-aware.

### 2.11 `backend/routes/dashboard.py` & `export.py` — contracts expiring *today* were excluded — **Medium**
`expiryDate` is stored as a date-only string (`YYYY-MM-DD`), but the "expiring soon" queries compared it against a full ISO timestamp (`2026-09-28T23:40:38+00:00`). Lexicographically, `"2026-09-28" < "2026-09-28T..."`, so a contract expiring today failed the `$gte` bound and was omitted from the dashboard count and the analytics PDF.
**Fix:** compare against `strftime("%Y-%m-%d")` and exclude already-expired contracts.

### 2.12 `backend/routes/billing.py` — Stripe exception classes — **Medium**
Used `stripe.error.StripeError` / `stripe.error.SignatureVerificationError`. In Stripe SDK v14 (pinned in `requirements.txt`) these live at the top level (`stripe.StripeError`), so the `except` clauses would raise `AttributeError` while handling an error.
**Fix:** switched to `stripe.StripeError` and `stripe.SignatureVerificationError`.

### 2.13 `backend/services/storage_service.py` — blocking boto3 calls in async code — **Medium**
`put_object`, `delete_object`, `head_bucket`, `create_bucket`, and `generate_presigned_url` are synchronous and were awaited directly inside `async def` functions, blocking the event loop for the duration of every S3 round-trip.
**Fix:** wrapped each call in `asyncio.to_thread(...)`.

### 2.14 `backend/server.py` — CORS allow-list could contain a bogus origin — **Low**
`os.environ.get('CORS_ORIGINS', '').split(',')` yields `['']` when the variable is unset, adding an empty-string origin to the allow-list.
**Fix:** parse defensively, drop empty entries, and log a warning when the list ends up empty.

### 2.15 `backend/routes/export.py` — unsanitized filename in `Content-Disposition` — **Low**
The contract title was interpolated directly into the download filename header.
**Fix:** sanitize to `[A-Za-z0-9._-]` before building the header.

### 2.16 `frontend/nginx.conf` — security headers dropped on static assets — **Low**
The static-asset `location` block declared its own `add_header Cache-Control`, which (per nginx semantics) suppresses inheritance of the server-level `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, and `Referrer-Policy` headers for every JS/CSS/image response.
**Fix:** re-declared the security headers inside the block with the `always` flag.

### 2.17 `backend/routes/contracts.py` — dead imports — **Low**
`check_trial_access` and `increment_trial_usage` were imported but never used (the atomic reservation path replaced them).
**Fix:** removed the unused imports.

### 2.18 Stale root `yarn.lock` — **Low**
An empty, autogenerated `yarn.lock` sat at the repository root while the real lockfile lives in `frontend/`. It could mislead tooling into resolving dependencies from the wrong directory.
**Fix:** removed the root file.

---

## 3. Observations (not changed)

- **Dead agentic subsystem.** `backend/services/{intake,obligation,risk,playbook,agent}_tasks.py` and `backend/routes/agentic.py` are intentionally stripped from launch (the router is not mounted in `server.py`). `flake8` reports 4 `F821 undefined name` errors in those modules (`ContractIntake`, `ObligationAlert`, `send_notification`, `unique_mitigations`). They are unreachable at runtime and were left untouched to avoid changing out-of-scope code.
- **`backend_test.py` and `backend/tests/test_lexisense_api.py`** assert that `GET /api/health` (no `v1`) returns 404 — consistent with the corrected prod healthcheck path.
- **`frontend/package.json`** declares both `yarn.lock` and `package-lock.json`; the Dockerfile uses `yarn install --frozen-lockfile`, so the npm lockfile is redundant but harmless.

---

## 4. Verification performed

| Check | Command | Result |
|-------|---------|--------|
| Backend syntax | `python3 -m py_compile backend/**/*.py` | ✅ Pass |
| Backend lint (critical) | `flake8 backend --select=E9,F63,F7,F82` | ✅ 0 errors in all edited files; 4 pre-existing `F821` isolated to the unmounted agentic modules |
| Frontend syntax | `node --check frontend/src/api.js` | ✅ Pass |
| `.env.example` trackable | `git add -n .env.example backend/.env.example frontend/.env.example` | ✅ All three stageable |
| Real `.env` still ignored | `git check-ignore -v backend/.env` | ✅ Ignored by `backend/.gitignore` |

---

## 5. Files changed

```
.env.example                          (new)
backend/.env.example                  (new)
frontend/.env.example                 (new)
.gitignore                            (fixed .env.* negation + removed corrupted/duplicate lines)
yarn.lock                             (removed - stale root lockfile)
backend/server.py                     (CORS origin parsing)
backend/seed_database.py              (broken import + isDefault persistence)
backend/models/contract.py            (new ContractUpdate body model)
backend/models/invitation.py          (new AcceptInvitationRequest body model)
backend/routes/contracts.py           (regex escaping, status-filter fix, PATCH body + RBAC, dead imports)
backend/routes/team.py                (accept-invite body model + tz-aware expiry)
backend/routes/billing.py             (Stripe exception classes)
backend/routes/dashboard.py           (date-only expiry comparison)
backend/routes/export.py              (date-only expiry comparison + filename sanitization)
backend/services/storage_service.py   (asyncio.to_thread for blocking boto3 calls)
frontend/src/api.js                   (undefined base URL guard + accept-invite body)
frontend/nginx.conf                   (security headers on static assets)
docker-compose.yml                    (env_file -> backend/.env)
docker-compose.prod.yml               (env_file -> backend/.env, healthcheck -> /api/v1/health)
```

---

*Review performed against revision `59476d9`. All fixes are contained in the `LexiSense_v2/` directory; the original `LexiSense/` checkout is unmodified.*
