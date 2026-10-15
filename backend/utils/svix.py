"""Svix webhook signature verification (the scheme Resend uses).

signed_content = f"{svix-id}.{svix-timestamp}.{raw_body}"
signature      = base64(HMAC-SHA256(base64decode(secret without "whsec_"), signed_content))
The ``svix-signature`` header holds space-separated ``v1,<sig>`` entries.
Implemented locally to avoid a new dependency; mirrors the official library.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Mapping, Optional

TOLERANCE_SECONDS = 5 * 60


class WebhookVerificationError(Exception):
    pass


def _key(secret: str) -> bytes:
    if secret.startswith("whsec_"):
        secret = secret[len("whsec_"):]
    try:
        return base64.b64decode(secret)
    except Exception as exc:
        raise WebhookVerificationError("invalid webhook secret") from exc


def sign(secret: str, msg_id: str, timestamp: str, body: bytes) -> str:
    content = f"{msg_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(_key(secret), content, hashlib.sha256).digest()).decode()


def verify(secret: str, headers: Mapping[str, str], body: bytes, now: Optional[float] = None) -> None:
    """Raise :class:`WebhookVerificationError` unless the request is authentic and fresh."""
    msg_id = headers.get("svix-id") or headers.get("webhook-id")
    timestamp = headers.get("svix-timestamp") or headers.get("webhook-timestamp")
    signatures = headers.get("svix-signature") or headers.get("webhook-signature")
    if not (msg_id and timestamp and signatures):
        raise WebhookVerificationError("missing signature headers")
    try:
        ts = int(timestamp)
    except ValueError:
        raise WebhookVerificationError("invalid timestamp")
    if abs((now or time.time()) - ts) > TOLERANCE_SECONDS:
        raise WebhookVerificationError("timestamp outside tolerance")
    expected = sign(secret, msg_id, timestamp, body).split(",", 1)[1]
    for entry in signatures.split():
        version, _, sig = entry.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return
    raise WebhookVerificationError("no matching signature")
