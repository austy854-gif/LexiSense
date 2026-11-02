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

import os
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

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


def _trusted_proxy_count() -> int:
    try:
        return max(0, int(os.environ.get("TRUSTED_PROXY_COUNT", "1")))
    except ValueError:
        return 1


def client_key(request) -> str:
    """Best-effort client identity for rate-limit keys.

    ``X-Forwarded-For`` is a list the client can pre-fill, so the *left-most*
    entry is spoofable. Each trusted proxy appends the address it saw, so the
    real client is the entry ``TRUSTED_PROXY_COUNT`` hops from the right
    (default 1: one platform proxy in front of the app). ``0`` ignores the
    header entirely and uses the socket peer. If the header is shorter than
    the configured hop count, the header can't be trusted and the socket peer
    is used. Repeated ``X-Forwarded-For`` headers are joined in order.

    ``request.client`` must be the raw socket peer for this to be sound, so
    uvicorn must NOT be told to trust every proxy (``--forwarded-allow-ips *``
    makes it rewrite the peer from the spoofable left-most entry). The
    Dockerfile defaults FORWARDED_ALLOW_IPS to 127.0.0.1.
    """
    hops = _trusted_proxy_count()
    client = getattr(request, "client", None)
    peer = getattr(client, "host", None) or "unknown"
    if not hops:
        return peer
    headers = request.headers
    values = headers.getlist("x-forwarded-for") if hasattr(headers, "getlist") else [headers.get("x-forwarded-for")]
    parts = [p.strip() for v in values if v for p in v.split(",") if p.strip()]
    if len(parts) >= hops:
        return parts[-hops]
    return peer


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


def auth_rate_limit(scope: str):
    """Per-IP limit for login, register, and password reset.

    Always enforces the in-process window (what the unit suite resets). When
    ``RATE_LIMIT_REDIS_URL`` or ``REDIS_URL`` is set, the same key is also
    counted in Redis so the limit holds across workers.
    """
    async def _dependency(request: Request):
        key = f"{scope}:{client_key(request)}"
        allowed, retry_after = AUTH_RATE_LIMITER.check(key)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again later.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        shared = _shared_auth_limiter()
        if shared is None:
            return
        try:
            allowed, retry_after = await shared.acheck(key)
        except RateLimiterUnavailable:
            logger.warning("Shared auth rate limiter unavailable; using the in-process limit only")
            return
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again later.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return _dependency


_shared_auth_limiter_cache = None


def _shared_auth_limiter():
    """Redis limiter, or ``None`` when Redis is not configured. Cached."""
    global _shared_auth_limiter_cache
    if _shared_auth_limiter_cache is None:
        candidate = make_org_limiter(10, 60.0, "auth-ip")
        _shared_auth_limiter_cache = candidate if isinstance(candidate, RedisRateLimiter) else False
    return _shared_auth_limiter_cache or None


#: Auth endpoints: 10 attempts / minute / client IP.
AUTH_RATE_LIMITER = SlidingWindowRateLimiter(
    max_requests=10, window_seconds=60.0, max_keys=10_000
)

#: Invitation acceptance: lower ceiling, the token is the only secret.
INVITE_RATE_LIMITER = SlidingWindowRateLimiter(
    max_requests=10, window_seconds=60.0, max_keys=5_000
)


# ---------------------------------------------------------------------------
# Per-organisation limits for expensive/background-work endpoints
# ---------------------------------------------------------------------------
class RateLimiterUnavailable(Exception):
    """Raised by a fail-closed limiter when its shared store is unreachable."""


class RedisRateLimiter:
    """Fixed-window limiter shared across replicas via Redis (INCR + EXPIRE).

    Window semantics: counts reset at fixed wall-clock boundaries
    (``floor(now / window)``). A caller can therefore spend the full budget at
    the end of one window and again at the start of the next, i.e. up to
    **2x the budget within a short span straddling a boundary**. That is an
    accepted trade-off for a single INCR per request; budgets are sized with it
    in mind.

    Redis failure handling:

    * connect and socket timeouts are below one second, so a dead Redis cannot
      stall a request for long;
    * a circuit breaker: after any Redis error the limiter skips Redis for
      ``breaker_seconds`` (default 30s) instead of paying the timeout on
      every request;
    * while Redis is skipped/unreachable, ``fail_closed=False`` limiters fall
      back to an in-process sliding window with the same budget, while
      ``fail_closed=True`` limiters (bulk jobs) raise
      :class:`RateLimiterUnavailable`, which the dependency maps to 503.
    """

    def __init__(
        self,
        redis_url: str,
        max_requests: int,
        window_seconds: float = 60.0,
        prefix: str = "rl",
        *,
        fail_closed: bool = False,
        breaker_seconds: float = 30.0,
        socket_timeout: float = 0.5,
        connect_timeout: float = 0.5,
        clock=time.monotonic,
    ) -> None:
        import redis.asyncio as aioredis

        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self.prefix = prefix
        self.fail_closed = fail_closed
        self.breaker_seconds = float(breaker_seconds)
        self._clock = clock
        self._open_until = 0.0
        self._redis = aioredis.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=connect_timeout,
            retry_on_timeout=False,
        )
        self._fallback = SlidingWindowRateLimiter(max_requests, window_seconds)

    @property
    def breaker_open(self) -> bool:
        return self._clock() < self._open_until

    def _degraded(self, key: str) -> Tuple[bool, float]:
        if self.fail_closed:
            raise RateLimiterUnavailable("rate limiter store unavailable")
        return self._fallback.check(key)

    async def acheck(self, key: str) -> Tuple[bool, float]:
        if self.breaker_open:
            return self._degraded(key)
        window = int(self.window_seconds)
        bucket = int(time.time() // window)
        rkey = f"{self.prefix}:{key}:{bucket}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(rkey)
            pipe.expire(rkey, window + 1)
            count, _ = await pipe.execute()
        except Exception as exc:
            self._open_until = self._clock() + self.breaker_seconds
            logger.warning(
                "Redis rate limiter unavailable; skipping Redis for %ss (%s): %s",
                self.breaker_seconds, "fail closed" if self.fail_closed else "in-process fallback", exc,
            )
            return self._degraded(key)
        if count > self.max_requests:
            return False, float(window - (time.time() % window))
        return True, 0.0

    def reset(self) -> None:
        self._fallback.reset()
        self._open_until = 0.0


class _InMemoryAsyncLimiter:
    """Async facade over :class:`SlidingWindowRateLimiter`."""

    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self._inner = SlidingWindowRateLimiter(max_requests, window_seconds)

    async def acheck(self, key: str) -> Tuple[bool, float]:
        return self._inner.check(key)

    def reset(self) -> None:
        self._inner.reset()


def make_org_limiter(max_requests: int, window_seconds: float, prefix: str, *, fail_closed: bool = False):
    """Redis-backed when RATE_LIMIT_REDIS_URL/REDIS_URL points at Redis, else in-memory.

    ``fail_closed`` only applies when Redis is configured: with no Redis URL
    the limiter is per-process by design and never fails closed.
    """
    from utils.env import get_env

    url = get_env("RATE_LIMIT_REDIS_URL") or get_env("REDIS_URL")
    if url and url.startswith(("redis://", "rediss://")):
        return RedisRateLimiter(url, max_requests, window_seconds, prefix=prefix, fail_closed=fail_closed)
    return _InMemoryAsyncLimiter(max_requests, window_seconds)


def org_rate_limit(limiter, scope: str, user_dependency=None):
    """FastAPI dependency limiting ``scope`` per *organisation* (not per IP).

    Must run after authentication; it reads ``organizationId`` from the
    resolved current user.
    """
    from utils.auth import get_current_user  # local import: avoid a cycle

    # Resolve auth/role FIRST so unauthorised callers cannot burn the org budget.
    async def _dependency(current_user: dict = Depends(user_dependency or get_current_user)):
        org = current_user.get("organizationId") or "unknown"
        try:
            allowed, retry_after = await limiter.acheck(f"{scope}:{org}")
        except RateLimiterUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting is temporarily unavailable. Please try again shortly.",
                headers={"Retry-After": "30"},
            )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded for your organization. Please try again later.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        return current_user

    return _dependency
