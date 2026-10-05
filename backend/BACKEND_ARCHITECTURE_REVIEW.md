# LexiSense — Backend Architecture Review & Implemented Changes

**Scope reviewed:** `backend/` (FastAPI + MongoDB/Motor) on
`fix/code-review-fixes` @ `e8b3433`, plus `test/patch-rbac-accept-invite`
(`5164cfc`, `c46e450`, `c9a737c`).

**Working branch:** `feat/backend-architecture-improvements` (contains both of
the above; the test branch already builds on `e8b3433`).

**Verification:** `pytest tests/unit -q` → **64 passed** (was 64 passed before
the changes; the full suite still passes after them).

---

## 1. Assessment summary

| Area | Verdict | Highest-value problem found |
|---|---|---|
| API layer | Structured but incomplete | Unbounded list queries (`.to_list(1000)`) silently truncated results; no correlation id surfaced to clients; bare `{"detail": …}` errors; health check leaked raw driver/proxy error text anonymously |
| Data models & schema | Reasonable models, incomplete indexing | No `id` indexes (every by-id endpoint was a COLLSCAN); unused index definitions; `expiration_alerts.createdAt` queried but never indexed |
| Auth & RBAC | **Largest gap** | Identity and role were trusted from the JWT body for the token's whole lifetime — demotion/deactivation/removal had **no effect**; roles were hard-coded per router; `restore` version endpoint had **no authorisation at all** |
| Invitation flow | Functional, racy | Non-atomic accept (two concurrent requests could both create accounts); duplicate-email accept raised an unhandled 500; tokens written to logs |
| Scalability & reliability | Services exist, wiring is thin | Storage/health probes were untuned; Mongo pool/timeouts left at driver defaults; deprecated `on_event` lifecycle meant startup work was skipped under an ASGI test client |

---

## 2. What was implemented

### 2.1 Authentication — database-authoritative identity (highest value)
`utils/auth.py`

* `get_current_user` now **re-reads the account on every authenticated request**
  and takes `role`, `organizationId` and `isActive` from the stored document
  instead of the token body. Before this, a demoted admin kept admin rights
  until their token expired (up to 24h by default), and a removed or
  deactivated member retained full access indefinitely. Cost is one indexed
  point read (`users.id`).
* Roles pass through `normalize_role()` on the way in, so a forged/stale role
  value can never be more privileged than `viewer`.
* Unknown user and invalid token now return the **same 401** — an
  unauthenticated caller can no longer probe which account ids exist.
* Deactivated accounts are rejected with 403 on every request.
* Tokens now carry and verify `iss`/`aud`/`nbf`/`id`-adjacent claims
  (`iss`, `aud`, `iat`, `nbf`, `jti`), so a token minted for another service
  sharing the secret cannot be replayed here; `jti` gives each session a unique
  revocable id for a future deny-list.
* Secret hygiene: a missing `JWT_SECRET` already failed fast; now a secret
  shorter than 32 chars is a **hard failure in production/staging** and a loud
  warning elsewhere.

### 2.2 RBAC — one registry, enforced everywhere
`utils/rbac.py` (new), `routes/team.py`, `routes/contracts.py`, `routes/audit.py`

* `ROLES = (viewer, user, manager, admin)` plus `ADMIN_ROLES`, `WRITE_ROLES`,
  `AUDIT_READER_ROLES` are now the single source of truth. The role list was
  previously duplicated as inline literals in at least four places.
* All five team-membership endpoints now use `require_role(*ADMIN_ROLES)`
  instead of an inline `current_user["role"] != "admin"` check.
* **Invitation roles are validated at issue time** (`POST /team/invite`).
  Previously any string was accepted, stored, and then copied verbatim onto the
  new account — an unvalidated path into the role column.
* **`POST /contracts/{id}/restore/{version}` now enforces write authorisation.**
  This was a genuine privilege gap: any authenticated member, including a
  read-only `viewer`, could roll a contract back to an arbitrary version. It
  now requires `admin`/`manager` or uploader ownership.
* `GET /audit` returned `200 []` to unauthorised callers, which is
  indistinguishable from "no audit history". It now returns a proper 403.

### 2.3 Database schema & indexes
`utils/database.py` (new), `server.py`

* Index creation moved out of a 16-line block in `server.py` into one
  declarative specification with a documented rationale per index.
* **Added the missing `id` unique indexes** on `users`, `contracts`,
  `organizations` (every by-id lookup — the majority of endpoints — was
  previously a collection scan), plus `users(organizationId, createdAt)`,
  `contracts(organizationId, status)`, `contracts(organizationId, uploadedBy)`,
  `invitations(organizationId, createdAt)`, `expiration_alerts(organizationId,
  createdAt)`, `audit_logs(organizationId, action)`,
  `chat_history(contractId, createdAt)`,
  `organization_billing(organizationId)` **unique** and
  `organization_billing(stripeCustomerId)` (sparse, backs the Stripe webhook
  path), `notifications(userId, isRead)`.
* Index failures are logged and counted rather than raised, so one problematic
  collection (e.g. legacy duplicate values blocking a unique index) cannot stop
  the application from booting.

### 2.4 API layer
`utils/pagination.py` (new), `utils/errors.py` (new), `server.py`,
`routes/contracts.py`, `routes/audit.py`

* **Bounded pagination with totals.** `GET /contracts` accepts `limit`
  (1–200, default 50) and `offset`, and returns `X-Total-Count`,
  `X-Page-Limit`, `X-Page-Offset`. The previous `.to_list(1000)` hard ceiling
  silently dropped every contract past the first 1000 and let one request
  materialise up to 1000 full documents.
* **Date filters are normalised.** `date_from`/`date_to` accept a bare date or
  a full ISO timestamp; a date-only upper bound is widened to the end of that
  UTC day, so `createdAt <= 2026-09-30` no longer excludes everything created
  on 2026-09-30. (The same class of bug that `e8b3433` fixed for `expiryDate`.)
* Invalid dates now return 400 instead of building a nonsense Mongo filter.
* **Uniform error envelope** on every error path — `HTTPException`,
  validation failures and unhandled exceptions:
  `{"error": {"code": "...", "message": "...", "details": {...}},
  "requestId": "...", "status": 404}`. `detail` is preserved for backwards
  compatibility with the existing frontend. Internal exception text is never
  returned for 5xx; the correlation id is.
* **`X-Request-ID` propagation.** An inbound `X-Request-ID` is honoured
  (validated against a strict character allow-list to prevent header/log
  injection) or generated, stored in the existing `request_id_var` contextvar
  and echoed on the response — so a user-reported failure can be tied to a
  trace. The CORS `expose_headers` list now includes it alongside the
  pagination headers.
* **Health check hardened.** The boto3 `head_bucket` probe (synchronous, was
  called directly on the event loop) now runs via `asyncio.to_thread` under a
  bounded timeout, as does the Mongo `ping`. Raw exception strings —
  which can disclose internal hostnames, endpoints and proxy config — are no
  longer returned by this **unauthenticated** endpoint; failures are reported
  as a status and logged with detail server-side.
* `GET /audit` gained a bounded `limit` (`ge=1, le=500`).

### 2.5 Invitation flow integrity
`routes/team.py`

* **Acceptance is atomic and single-use.** The `pending → accepted` transition
  is a conditional `update_one`, so two concurrent requests with the same token
  cannot both create an account. Previously both could pass the
  `find_one(status="pending")` check and both insert a user.
* A duplicate account for the invited address no longer raises an unhandled
  500 — it is claimed idempotently and returns the same generic response (which
  also stops the endpoint being used to enumerate existing accounts).
* If the user insert fails after the claim, the invitation is released back to
  `pending` so a transient write error cannot strand it permanently.
* **Invitation tokens are no longer written to logs** (`logger.info` included
  the raw token).
* Claiming, cancel, role change and removal now write audit entries.

### 2.6 Reliability, security & lifecycle
`server.py`, `routes/auth.py`

* **Mongo connection pool and timeouts are explicit**: `maxPoolSize` 50
  (the driver default of 100 per worker can exhaust a standard Atlas tier),
  `maxIdleTimeMS=45s` (retires sockets before an idle proxy closes them),
  `connect=False` (lazy connect so a database blip at boot cannot prevent the
  process from starting and serving health checks), and
  `serverSelectionTimeoutMS=5s` (fail fast instead of hanging requests while
  the primary is unreachable). All overridable via env.
* **Rate limiting on the auth endpoints** (`POST /auth/login`,
  `POST /auth/register`, `POST /team/accept-invite`): 10 requests/minute per
  client key, sliding window, 429 with `Retry-After`. The limiter is
  memory-bounded and fails safe when its key space is exhausted.
* **Modern lifespan protocol** replaces the deprecated `@app.on_event`
  handlers, so startup work (indexes, bucket check, migrations) actually runs
  under an ASGI test client and in the ASGI servers we deploy with.

---

## 3. Deliberately not changed (and why)

* **Agentic subsystem** remains stripped for launch (per `PRODUCT_SCOPE.md`).
* **`originalText` storage**: contract text is still embedded in the contract
  document. At scale this bloats the working set (MongoDB documents are capped
  at 16 MB and large text fields hurt cache locality), and the list endpoint
  already has to project it out. Moving it to a dedicated collection or S3 is
  the right follow-up, but it is a data migration, not an in-scope fix.
* **`GET /team/members` unbounded `.to_list(1000)`**: left as-is to keep this
  change reviewable; it is on the list for the pagination follow-up.
* **Distributed rate limiting**: the limiter is per worker process. With `N`
  replicas the effective limit is `N × 10`/min. Acceptable as a brute-force
  speed bump and it fails safe, but a horizontally scaled deployment should
  move it behind Redis.

---

## 4. Test coverage added / gaps handed off

The existing regression suite (`tests/unit/`) is preserved and passing. Two
adjustments were required:

* `conftest.py` gained an autouse fixture resetting the process-global rate
  limiters between tests (they are keyed by client identity and the in-process
  test client always presents the same host, so without the reset later tests
  correctly received 429 and the suite became order-dependent).

**Known gaps handed to API Tester** (see handoff): no coverage yet for
DB-authoritative role resolution (demotion while a token is live), the restore
endpoint's new authorisation, pagination bounds/`X-Total-Count`, the 403 audit
semantics, and the auth rate limiter itself.
