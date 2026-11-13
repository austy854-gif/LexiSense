# Entitlements and roles

Two server-side tables decide who may call each API route. The UI reads them
through `GET /billing/capabilities` but never enforces them; the backend does.

## Role matrix (`backend/utils/policy.py`)

`ROUTE_POLICIES` has one row per endpoint (`module.function`) naming the
capability it needs. `CAPABILITIES` maps each role to its capabilities.
`utils.auth.get_current_user` enforces the row for every authenticated request,
so no authenticated route can skip it. A route with no row is refused with
**403** (deny by default). Unknown or corrupt roles are treated as `viewer`.

| Capability | viewer | user | manager | admin |
|---|:-:|:-:|:-:|:-:|
| Read org data, own session and notifications, AI chat on a readable contract | ✓ | ✓ | ✓ | ✓ |
| Upload contracts, submit for review | | ✓ | ✓ | ✓ |
| Edit, tag or restore a contract | | own uploads | ✓ | ✓ |
| Delete, approve, reject, activate contracts | | | ✓ | ✓ |
| View the audit log, trigger alert scans | | | ✓ | ✓ |
| Alert settings, templates | | | | ✓ |
| Invite and manage members, billing (checkout and portal) | | | | ✓ |
| Agentic: run risk/obligation tasks, playbook analysis, update obligations | | ✓ | ✓ | ✓ |
| Agentic: manage agents and playbooks, apply redlines, process intake, bulk runs | | | ✓ | ✓ |
| Agentic: delete agents and playbooks | | | | ✓ |

Agentic routes are only mounted when their feature flags are on.

## Entitlement grid (`backend/utils/billing_access.py`)

`ENTITLEMENT_GRID` maps the organization's billing state (from
`compute_entitlement`) to the features it may use. Each route's policy row
names the feature its handler enforces (`assert_org_writable`,
`ensure_ai_access`, `ensure_invite_access`, or the upload reservation).

| Billing state | upload | ai | write | invite |
|---|:-:|:-:|:-:|:-:|
| `paid` (active or trialing subscription on a configured price) | ✓ | ✓ | ✓ | ✓ |
| `grace` (past due, within `BILLING_GRACE_DAYS`) | ✓ | ✓ | ✓ | ✓ |
| `trial` | ✓ (trial counter) | ✓ | ✓ | ✓ |
| `trial_expired` | | | ✓ | ✓ |
| `past_due` (after grace) | | | | |
| `cancelled` | | | | |
| `unverified_plan` (price not in config) | | | | |

Every paid plan (Starter, Team, Business) gets the same features. Plans differ
only in their seat cap (`backend/utils/plans.py`): trial 3, Starter 5, Team 15,
Business 30, enforced when inviting (pending invitations count as seats).

Routes without a feature (reads, billing checkout and portal, team management,
notifications) keep working in every state, so a read-only organization can
still pay, export its data or remove members.

Both role and billing denials return **403** with a message, matching the
existing billing gates and the SPA's error handling. No route returns 402.

## Tests

`backend/tests/unit/test_entitlement_role_grid.py` checks that every mountable
route has exactly one row, pins the role matrix, and runs every authenticated
route against every role and every billing state in an in-process app.
