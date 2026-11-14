"""Upload size limit, enforced before the body is buffered.

``MAX_UPLOAD_MB`` (default 10, clamped to 1..100) caps each uploaded file.
Two layers enforce it:

* :class:`UploadSizeLimitMiddleware` guards the upload routes at the ASGI
  level. A ``Content-Length`` over the request budget is refused with 413
  before any body is read; a chunked or under-declared body is counted while
  it streams and cut off with 413 as soon as it passes the budget. The budget
  is one file plus multipart overhead for ``POST /contracts`` and
  ``BULK_MAX_FILES`` files plus overhead for ``POST /contracts/bulk``.
* The handlers check each part's size (:func:`ensure_within_upload_limit`)
  before reading it into memory, reserving trial quota or touching storage,
  so an oversized file never leaves quota or an object behind.
* After extraction, :func:`ensure_text_fits` refuses text over
  ``MAX_EXTRACTED_TEXT_BYTES`` (it is stored inline on the contract document,
  which MongoDB caps at 16 MiB), again before quota or storage.
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException, status

from utils.env import get_env

logger = logging.getLogger(__name__)

DEFAULT_MAX_UPLOAD_MB = 10
MIN_UPLOAD_MB = 1
MAX_UPLOAD_MB_CEILING = 100
#: Files accepted by one bulk upload request.
BULK_MAX_FILES = 10
#: Room for multipart boundaries, headers and the small form fields.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
#: Extracted text is stored inline on the contract document, and MongoDB caps a
#: document at 16 MiB. Leave room for the AI analysis and the other fields.
MAX_EXTRACTED_TEXT_BYTES = 12 * 1024 * 1024
TEXT_TOO_LARGE_MESSAGE = (
    "This file has too much text to store as one contract (12 MB of text at most)."
)

API_PREFIX = "/api/v1"
SINGLE_UPLOAD_PATHS = frozenset({f"{API_PREFIX}/contracts", f"{API_PREFIX}/contracts/"})
BULK_UPLOAD_PATHS = frozenset(
    {f"{API_PREFIX}/contracts/bulk", f"{API_PREFIX}/contracts/bulk/"}
)


def max_upload_mb() -> float:
    """The configured per-file limit in MB (``MAX_UPLOAD_MB``)."""
    raw = get_env("MAX_UPLOAD_MB")
    if not raw:
        return float(DEFAULT_MAX_UPLOAD_MB)
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "MAX_UPLOAD_MB=%r is not a number; using %s", raw, DEFAULT_MAX_UPLOAD_MB
        )
        return float(DEFAULT_MAX_UPLOAD_MB)
    if value != value:  # NaN
        return float(DEFAULT_MAX_UPLOAD_MB)
    return min(max(value, MIN_UPLOAD_MB), MAX_UPLOAD_MB_CEILING)


def max_upload_bytes() -> int:
    """The per-file limit in bytes."""
    return int(max_upload_mb() * 1024 * 1024)


def too_large_message() -> str:
    mb = max_upload_mb()
    shown = int(mb) if mb == int(mb) else mb
    return f"File too large. The maximum upload size is {shown} MB."


def too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=too_large_message(),
    )


def ensure_within_upload_limit(upload) -> None:
    """Raise 413 when ``upload`` (a Starlette ``UploadFile``) is over the limit."""
    size = getattr(upload, "size", None)
    if size is not None and size > max_upload_bytes():
        raise too_large()


def ensure_text_fits(text: str) -> None:
    """Raise 413 when extracted ``text`` would not fit in a contract document."""
    if len(text.encode("utf-8", errors="surrogatepass")) > MAX_EXTRACTED_TEXT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=TEXT_TOO_LARGE_MESSAGE,
        )


def request_body_limit(path: str):
    """Byte budget for a request to ``path``, or ``None`` when not an upload route."""
    if path in SINGLE_UPLOAD_PATHS:
        return max_upload_bytes() + MULTIPART_OVERHEAD_BYTES
    if path in BULK_UPLOAD_PATHS:
        return max_upload_bytes() * BULK_MAX_FILES + MULTIPART_OVERHEAD_BYTES
    return None


class _BodyTooLarge(HTTPException):
    """Raised from ``receive`` once a streamed body passes its budget."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=too_large_message(),
        )


async def _send_413(send) -> None:
    from utils.errors import _envelope

    body = json.dumps(_envelope(413, too_large_message())).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class UploadSizeLimitMiddleware:
    """Pure ASGI middleware: 413 for upload bodies over their budget."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        limit = request_body_limit(scope.get("path", ""))
        if limit is None:
            return await self.app(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > limit:
                    return await _send_413(send)
                break

        received = 0
        started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise
            await _send_413(send)
