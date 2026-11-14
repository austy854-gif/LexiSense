# End-to-end tests (Playwright)

The `e2e/` package drives the real React build in Chromium against the real
FastAPI app and a real MongoDB. Only third-party services are faked. The suite
runs in CI as the **End-to-End Tests (Playwright)** job, and both deploy jobs
wait for it.

## What is covered

| Spec | Flow |
| --- | --- |
| `auth.spec.js` | Sign up through `/register`; log in (a wrong password is refused, then the right one works); log out, and protected pages send you back to `/login`. |
| `upload.spec.js` | A user uploads a contract through the dialog, opens it from the toast and sees the analysis (summary, parties, key terms, risks). An upload over `MAX_UPLOAD_MB` shows the 413 message. |
| `workflow.spec.js` | The uploader submits for review (and cannot approve it); a manager approves and activates. A manager can also reject back to draft. |
| `roles.spec.js` | A viewer can read contracts and the team list, but sees no upload, bulk upload, workflow or invite controls. The API returns 403 to a viewer upload even when the UI is bypassed. |
| `billing.spec.js` | A past-due org is read-only on `/contracts` and `/billing`. The billing portal button hands off to Stripe (stubbed). Writes return 403 with the billing reason, the UI shows that reason in a toast, and the user stays signed in. |
| `invite.spec.js` | An admin invites a manager; the invitee opens the emailed link, sets a password and lands on the dashboard; the link cannot be reused; the admin sees the new member. Non-admins cannot invite (UI and API). |

## How it runs

- `e2e/scripts/start-backend.sh` starts `uvicorn e2e.stub_app:app` on
  `127.0.0.1:8000` with fixture-only settings and unsets every real key
  (`EMERGENT_LLM_KEY`, `RESEND_API_KEY`, AWS, Stripe, Redis, Celery, Sentry).
  The CI job also blanks the workflow-level secrets.
- The backend serves the built SPA from `FRONTEND_BUILD_DIR`. The build uses
  `REACT_APP_BACKEND_URL=http://localhost:8000`, so the browser and the API are
  on the same origin and no CORS or proxy setup is needed. CI reuses the
  `frontend-build` artifact from the Frontend Lint & Build job.
- `backend/e2e/stub_app.py` imports the normal `server.app` and only patches
  third-party calls. It refuses to load unless `E2E_STUB_SERVICES=1` and
  `ENVIRONMENT` is not `production`, and it is never imported by `server.py`.
- Each test starts with `POST /__e2e/reset`, which empties the e2e database and
  seeds two organizations:
  - **Acme Legal**: Team plan, active, with an admin, manager, user and viewer.
  - **Overdue LLC**: Team plan, `past_due` for 30 days (past the grace
    window), with an admin and one contract.

  All fixture users share the password `E2e-Passw0rd!` and use
  `*.example.com` addresses (email validation rejects `.test`).
- Most tests sign in with `POST /__e2e/session`, which issues a normal JWT for
  a fixture user. Only `auth.spec.js` uses the login form, so the per-IP auth
  rate limit (10/min) is not hit. The reset also clears the in-memory limiters.
- One worker, tests run in order, one retry in CI. Traces and screenshots are
  kept only for failed tests and uploaded as the `playwright-report` artifact.

## Stubs and why

| Service | Stub | Why |
| --- | --- | --- |
| AI analysis and chat (Emergent / LiteLLM) | In-process: `analyze_contract` returns a fixed analysis; chat returns a fixed answer. | Real calls are slow, cost money, need a key and are not deterministic. |
| S3 storage | In-process: upload, delete, purge and presigned URLs use an in-memory dict. | Needs AWS credentials; nothing here tests S3 itself. |
| Invitation email (Resend) | In-process: emails are captured and exposed at `GET /__e2e/outbox`. | The invite test needs the accept link without a real mailbox. |
| Stripe portal and checkout | **Network layer** with `page.route` in `billing.spec.js`: `POST /api/v1/billing/portal` returns a fake portal URL, and `billing.stripe.com` returns a stub page. | Creating a portal session needs a Stripe secret key and a real customer, and the page itself is Stripe's. Billing *state* is still real: it comes from `organization_billing` through the normal entitlement code. |

The `/__e2e/*` routes exist only in the stub app and are left out of the
OpenAPI schema.

## Running locally

Requirements: Python 3.13 with `backend/requirements.txt` installed, Node
22.18+, and MongoDB on `localhost:27017` (for example
`docker run -d -p 27017:27017 mongo:7`).

```bash
# 1. Build the frontend against the local API (once, or after UI changes)
cd frontend && REACT_APP_BACKEND_URL=http://localhost:8000 yarn build && cd ..

# 2. Install Playwright
cd e2e && npm ci && npx playwright install chromium

# 3. Run. Playwright starts the backend for you; E2E_PYTHON picks the
#    interpreter (for example backend/venv/bin/python).
E2E_PYTHON=../backend/venv/bin/python npx playwright test
npx playwright show-report      # after a failure, open the HTML report
```

To keep a backend running between runs, start `scripts/start-backend.sh`
yourself and run the tests with `E2E_SKIP_SERVER=1`. Restart it after changing
backend code. The tests write only to the `lexisense_e2e` database (override
with `E2E_DB_NAME`).

## Adding a test

- Call `resetData(request)` in `beforeEach`, and sign in with
  `signInAs(page, USERS.x)` unless the login form itself is under test.
- Select elements by `data-testid` or accessible role, not CSS classes.
- Wait on responses or visible state, never on fixed sleeps.
- Stub a third party at the network layer only when it cannot run locally, and
  add it to the table above.

## Next

- Export the OpenAPI schema in CI and check it for drift (left out of this
  change on purpose).
- Cover bulk upload, contract chat, version restore and the trial-expired
  state.
