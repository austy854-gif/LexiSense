"""Uniform API error envelope.

Every error response the application produces -- whether an ``HTTPException``
raised in a router, a 404 from the router itself, a validation failure, or an
unexpected exception -- is normalised to::

    {
      "error": {
        "code": "contract_not_found",
        "message": "Contract not found",
        "details": {...}          # optional, never leaks internals
      },
      "requestId": "9f3c...",
      "status": 404
    }

Before this module the API returned FastAPI's bare ``{"detail": ...}``, which
meant clients had to branch on HTTP status *and* parse prose to distinguish
"not found" from "forbidden". ``detail`` is kept in the envelope for backwards
compatibility with the existing frontend, but ``error.code`` is now the stable,
machine-readable contract.

Security note: internal exception text is never returned for 5xx responses --
only a correlation id, so an operator can find the traceback in the logs while
the caller learns nothing about the stack.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.logging_config import request_id_var

logger = logging.getLogger(__name__)

#: HTTP status -> stable machine-readable error code.
_STATUS_CODES: Dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limit_exceeded",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}

#: Prose detail -> stable code, so existing raise sites keep their wording.
_DETAIL_CODES: Dict[str, str] = {
    "Invalid or expired token": "invalid_token",
    "Invalid token payload": "invalid_token",
    "Invalid email or password": "invalid_credentials",
    "Account is deactivated": "account_deactivated",
    "Email already registered": "email_already_registered",
    "Invalid or expired invitation": "invalid_invitation",
    "Invitation has expired": "invitation_expired",
    "Contract not found": "contract_not_found",
    "Version not found": "version_not_found",
    "Member not found": "member_not_found",
    "Invitation not found": "invitation_not_found",
    "Invalid status value": "invalid_status",
}


def _current_request_id() -> str:
    return request_id_var.get() or ""


def _code_for(status_code: int, detail: Any) -> str:
    if isinstance(detail, str) and detail in _DETAIL_CODES:
        return _DETAIL_CODES[detail]
    return _STATUS_CODES.get(status_code, "error")


def _envelope(
    status_code: int,
    detail: Any,
    *,
    code: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "error": {
            "code": code or _code_for(status_code, detail),
            "message": detail,
        },
        "requestId": _current_request_id(),
        "status": status_code,
    }
    # Backwards compatibility: the pre-existing frontend reads `detail`.
    payload["detail"] = detail

    if extra:
        payload["error"]["details"] = extra

    return payload


def _error_headers(exc: Exception) -> Optional[Dict[str, str]]:
    headers = getattr(exc, "headers", None)
    return dict(headers) if headers else None


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the envelope handlers to ``app``."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        status_code = exc.status_code
        detail = exc.detail
        return JSONResponse(
            status_code=status_code,
            content=_envelope(status_code, detail),
            headers=_error_headers(exc),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        # Collapse pydantic's error list into safe, serialisable primitives.
        fields = []
        for err in exc.errors():
            location = [str(part) for part in err.get("loc", []) if part != "body"]
            fields.append(
                {
                    "field": ".".join(location) or "body",
                    "message": err.get("msg", "invalid value"),
                    "type": err.get("type", "value_error"),
                }
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Request validation failed",
                code="validation_error",
                extra={"fields": fields},
            ),
        )

    @app.exception_handler(HTTPException)
    async def _fastapi_http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.status_code, exc.detail),
            headers=_error_headers(exc),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        # Log the real error with the correlation id; return a safe message.
        logger.exception(
            "Unhandled exception",
            extra={
                "request_id": _current_request_id(),
                "method": request.method,
                "path": request.url.path,
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "An unexpected error occurred. Please retry; if it persists, "
                "contact support with the correlation id.",
                code="internal_error",
            ),
        )
