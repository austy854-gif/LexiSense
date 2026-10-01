"""Authentication primitives: password hashing, JWT issuance and the
authorisation dependencies used by every router.

Design decisions worth knowing
------------------------------
1. **The database is authoritative for identity and role.**
   ``get_current_user`` re-reads the account on every authenticated request
   and takes ``role``/``organizationId``/``isActive`` from the stored document
   rather than from the token body. A JWT remains valid for its full lifetime,
   so without this check a demoted user (``admin`` -> ``viewer``) or a removed
   member keeps their old privileges until the token expires, and deactivation
   has no effect at all. The extra lookup is a single indexed point read on
   ``users.id`` (see ``utils/database.py``).

   If the identity store has not been wired (``init_identity_store``), the
   dependency degrades to token-only verification. That mode exists purely so
   narrow in-process test apps can mount a single router; production always
   wires the store via ``routes.auth.init_db``.

2. **A forged or stale role can never be more privileged than ``viewer``.**
   Roles are normalised through ``utils.rbac`` on the way in.

3. **Tokens carry ``iss``/``aud``/``iat``/``nbf``/``jti``.**
   ``iss``/``aud`` are verified on decode so a token minted for another
   service that shares the secret cannot be replayed here, and ``jti`` gives
   every session a unique, revocable identity (needed for a future deny-list).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from utils.rbac import normalize_role

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

#: Symmetric secret. Fail fast: a missing secret must never silently become "".
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required but not set")

JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

#: Token audience/issuer, verified on every decode.
JWT_ISSUER = os.environ.get("JWT_ISSUER", "lexisense-api")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "lexisense-client")

#: HS256 is only as strong as the secret; refuse trivially short ones in
#: production. Development/test environments get a warning instead of a hard
#: failure so the suite keeps running.
_MIN_SECRET_LENGTH = 32
_ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
if len(JWT_SECRET) < _MIN_SECRET_LENGTH:
    if _ENVIRONMENT in ("production", "prod", "staging"):
        raise RuntimeError(
            f"JWT_SECRET must be at least {_MIN_SECRET_LENGTH} characters in {_ENVIRONMENT}"
        )
    logger.warning(
        "JWT_SECRET is shorter than %d characters; this is not safe for production",
        _MIN_SECRET_LENGTH,
    )

#: Identity store (the ``users`` collection). ``None`` => token-only mode.
_users_collection = None


def init_identity_store(database) -> None:
    """Wire the identity store from a Motor database handle."""
    global _users_collection
    _users_collection = database.users
    logger.info("Auth identity store wired to database")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Mint an access token.

    ``exp``/``iat``/``nbf``/``jti``/``iss``/``aud`` are set by this function and
    cannot be overridden by the caller.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode = dict(data)
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "nbf": now,
            "jti": str(uuid.uuid4()),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        }
    )
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Verify signature, expiry, issuer and audience. Raises 401 on failure."""
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Return the authenticated principal.

    The returned mapping is token-shaped (``sub``/``email``/``role``/
    ``organizationId``) so existing routers keep working, but ``role`` and
    ``organizationId`` are refreshed from the database when it is available.
    """
    payload = decode_token(credentials.credentials)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload["role"] = normalize_role(payload.get("role"))
    payload.setdefault("organizationId", None)

    if _users_collection is None:
        return payload

    user = await _users_collection.find_one(
        {"id": user_id},
        {
            "_id": 0,
            "id": 1,
            "email": 1,
            "role": 1,
            "organizationId": 1,
            "isActive": 1,
        },
    )

    # Same 401 for "unknown user" and "bad token": do not let an unauthenticated
    # caller probe which account ids exist.
    if user is None:
        raise _unauthorized()

    if not user.get("isActive", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    payload["sub"] = user["id"]
    payload["role"] = normalize_role(user.get("role"))
    payload["organizationId"] = user.get("organizationId")
    payload["email"] = user.get("email", payload.get("email"))

    return payload


def require_role(*allowed_roles: str):
    """Dependency factory that restricts access to specific roles."""

    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(allowed_roles)}",
            )
        return current_user

    return _check
