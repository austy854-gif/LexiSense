"""Invitation tokens: hashed at rest, raw value only in the emailed link.

An invitation token is a bearer secret -- whoever holds it can create an
account in the inviting organisation. Only ``sha256(token)`` (hex) is stored,
in ``invitations.tokenHash``; the raw token exists solely in the invite email
and is never persisted, logged or returned by the API.

Legacy records (created before hashing) carry the raw value in ``token``.
They are hashed in bulk by migration 003 at startup and, as a fallback for
records written by an older instance during a rolling deploy, lazily migrated
the first time they are looked up (:func:`find_invitation_by_token`).
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional, Tuple

#: Legacy plaintext field. Read only for migration; never written.
LEGACY_TOKEN_FIELD = "token"
TOKEN_HASH_FIELD = "tokenHash"


def hash_invite_token(raw_token: str) -> str:
    """SHA-256 hex digest of the raw token (what is stored and indexed)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_invite_token() -> Tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Persist only the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_invite_token(raw)


async def find_invitation_by_token(collection, raw_token: str, extra_filter: Optional[dict] = None):
    """Look an invitation up by its raw token via the stored hash.

    Falls back to a legacy plaintext record and migrates it in place
    (``tokenHash`` set, ``token`` removed) before returning it. The returned
    document never contains the plaintext token.

    Timing: the lookup is an equality match on a SHA-256 digest of the
    caller's input, so response time cannot be used to learn a stored token
    prefix. The legacy branch compares the raw value only until that record is
    migrated (bulk at startup), and the rate limiter on the accept endpoint
    bounds guessing either way.
    """
    if not isinstance(raw_token, str) or not raw_token:
        return None
    query = dict(extra_filter or {})
    token_hash = hash_invite_token(raw_token)

    invitation = await collection.find_one({**query, TOKEN_HASH_FIELD: token_hash}, {"_id": 0})
    if invitation:
        invitation.pop(LEGACY_TOKEN_FIELD, None)
        return invitation

    legacy = await collection.find_one({**query, LEGACY_TOKEN_FIELD: raw_token}, {"_id": 0})
    if not legacy:
        return None
    # Conditional on the plaintext still being present: idempotent and safe
    # against a concurrent migration of the same record.
    await collection.update_one(
        {"id": legacy["id"], LEGACY_TOKEN_FIELD: raw_token},
        {"$set": {TOKEN_HASH_FIELD: token_hash}, "$unset": {LEGACY_TOKEN_FIELD: ""}},
    )
    legacy.pop(LEGACY_TOKEN_FIELD, None)
    legacy[TOKEN_HASH_FIELD] = token_hash
    return legacy


async def hash_legacy_invite_tokens(collection) -> int:
    """Hash every invitation that still stores a plaintext token. Idempotent."""
    migrated = 0
    async for doc in collection.find(
        {LEGACY_TOKEN_FIELD: {"$exists": True}}, {"_id": 0, "id": 1, LEGACY_TOKEN_FIELD: 1}
    ):
        raw = doc.get(LEGACY_TOKEN_FIELD)
        update = {"$unset": {LEGACY_TOKEN_FIELD: ""}}
        if isinstance(raw, str) and raw:
            update["$set"] = {TOKEN_HASH_FIELD: hash_invite_token(raw)}
        result = await collection.update_one({"id": doc["id"], LEGACY_TOKEN_FIELD: raw}, update)
        migrated += result.modified_count
    return migrated
