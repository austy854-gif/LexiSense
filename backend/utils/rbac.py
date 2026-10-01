"""Single source of truth for application roles, plus a bounded in-process
rate limiter for abuse-prone endpoints.

Why this module exists
---------------------
Role strings were previously hard-coded in several routers (``"admin"``,
``("admin", "manager")``, ``["admin", "manager", "user", "viewer"]`` ...).
That makes the permission model impossible to review in one place and lets a
router silently invent a role. Every authorisation decision now resolves
against the registry below.

Roles (ordered least -> most privileged)::

    viewer   read-only; may never mutate contracts or identity
    user     may create/upload and edit their own contracts
    manager  may edit any contract in the organisation, read audit data
    admin    may manage team identity, billing and organisation settings
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request, status

ROLE_VIEWER = "viewer"
ROLE_USER = "user"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"

#: Canonical role registry. Ordered from least to most privileged.
ROLES: Tuple[str, ...] = (ROLE_VIEWER, ROLE_USER, ROLE_MANAGER, ROLE_ADMIN)

#: Least-privileged role: what an unknown/corrupt role value degrades to.
DEFAULT_ROLE = ROLE_VIEWER

#: Role assigned when the application creates an account itself
#: (organisation owner at registration, invitees with no explicit role).
DEFAULT_MEMBER_ROLE = ROLE_USER

#: Roles allowed to change membership, roles and organisation-wide settings.
ADMIN_ROLES: Tuple[str, ...] = (ROLE_ADMIN,)

#: Roles allowed to author/modify contracts (still subject to ownership rules).
WRITE_ROLES: Tuple[str, ...] = (ROLE_USER, ROLE_MANAGER, ROLE_ADMIN)

#: Roles allowed to read organisation-wide audit data.
AUDIT_READER_ROLES: Tuple[str, ...] = (ROLE_ADMIN, ROLE_MANAGER)


def is_valid_role(role: object) -> bool:
    """True when ``role`` is a known role string."""
    return isinstance(role, str) and role in ROLES


def normalize_role(role: object) -> str:
    """Map an unknown/invalid role to the least-privileged role.

    Called on every inbound JWT/session role so a forged or stale role value
    can never be *more* privileged than ``viewer``.
    """
    return role if is_valid_role(role) else DEFAULT_ROLE


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class SlidingWindowRateLimiter:
    """Bounded in-process sliding-window rate limiter.

    Scope and limitations (deliberate):

    * It protects **low-volume, high-abuse-risk** endpoints (login, register,
      password reset, invitation acceptance) where a brute-force attempt is
      far cheaper to block than to serve.
    * It is **per worker process**, so with ``N`` replicas the effective limit
      is ``max_requests * N``. That is acceptable as a first line of defence
      and fails safe for slow brute-force attacks, but a horizontally scaled
      deployment should move this behind Redis (see REVIEW doc, "Next steps").
    * The key space is bounded (``max_keys``): once full, stale keys are
      evicted and, if still full, new keys are refused rather than allowing
      unbounded memory growth from spoofed client identifiers.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: float = 60.0,
        max_keys: int = 10_000,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self.max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def _bucket(self, key: str, now: float) -> Deque[float]:
        bucket = self._hits[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def _evict_stale(self, now: float) -> None:
        cutoff = now - self.window_seconds
        if len(self._hits) < self.max_keys:
            return
        for key in [k for k, bucket in self._hits.items() if not bucket or bucket[-1] <= cutoff]:
            self._hits.pop(key, None)

    def check(self, key: str, now: Optional[float] = None) -> Tuple[bool, float]:
        """Record an attempt for ``key``.

        Returns ``(allowed, retry_after_seconds)``.
        """
        now = time.monotonic() if now is None else now

        if key not in self._hits and len(self._hits) >= self.max_keys:
            self._evict_stale(now)
            if len(self._hits) >= self.max_keys:
                # Refuse rather than grow without bound (fail-safe).
                return False, self.window_seconds

        bucket = self._bucket(key, now)
        if len(bucket) >= self.max_requests:
            retry_after = self.window_seconds - (now - bucket[0])
            return False, max(retry_after, 0.0)

        bucket.append(now)
        return True, 0.0

    def reset(self) -> None:
        """Drop all state (used by tests)."""
        self._hits.clear()


def client_key(request) -> str:
    """Best-effort client identity for rate-limit keys.

    ``X-Forwarded-For`` is honoured because the app is deployed behind a
    reverse proxy (nginx) that overwrites it; the left-most entry is the
    originating client. Behind a proxy that does **not** sanitise the header,
    this is spoofable -- that is precisely why the limiter is a defence in
    depth layer and not the only control.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def rate_limit(limiter: SlidingWindowRateLimiter, scope: str):
    """Build a FastAPI dependency enforcing ``limiter`` for ``scope``."""

    # NOTE: the parameter must be annotated with ``Request``; an unannotated
    # parameter is interpreted by FastAPI as a required query parameter, which
    # rejects every request with a 422 before the handler runs.
    async def _dependency(request: Request):
        allowed, retry_after = limiter.check(f"{scope}:{client_key(request)}")
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again later.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return _dependency


#: Auth endpoints: 10 attempts / minute / client IP.
AUTH_RATE_LIMITER = SlidingWindowRateLimiter(
    max_requests=10, window_seconds=60.0, max_keys=10_000
)

#: Invitation acceptance: lower ceiling, the token is the only secret.
INVITE_RATE_LIMITER = SlidingWindowRateLimiter(
    max_requests=10, window_seconds=60.0, max_keys=5_000
)
