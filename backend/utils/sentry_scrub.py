"""Sentry event scrubbing: keep bearer secrets and PII out of error reports.

Wired into ``sentry_sdk.init`` by ``server.py`` (see :func:`sentry_init_options`).
Local-variable capture is disabled there, and every event / breadcrumb passes
through :func:`before_send` / :func:`before_breadcrumb`, which redact:

* invite / approval / JWT-style tokens (long base64url runs),
* email addresses (``***@domain`` -- the domain is kept for triage),
* query-string values in any URL or ``?a=b&c=d`` fragment,
* sensitive keys anywhere (``token``, ``password``, ``authorization``,
  ``cookie``, ``html``, ...) and request cookies / auth headers wholesale.

The hooks never raise. If scrubbing fails, a minimal event (ids, level and
exception *types* only) is returned and the failure is logged; the original
content is never sent unscrubbed.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode

#: Not captured by Sentry (``server.py`` calls ``ignore_logger`` on it), so a
#: scrubber failure cannot recurse into another event.
logger = logging.getLogger("lexisense.sentry_scrub")

REDACTED = "[REDACTED]"
_MAX_DEPTH = 25

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
# Query values: ``?k=v`` / ``&k=v`` up to the next separator.
_QUERY_VALUE_RE = re.compile(r"([?&][^=\s&#?]+=)[^&\s#\"'<>]*")
# Long base64url runs: invite tokens (token_urlsafe(32) = 43 chars), approval
# token halves, JWT segments, API keys.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{32,}")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Sentry's own ids (event/trace ids are 32 hex, commit SHAs 40 hex).
_SAFE_HEX_RE = re.compile(r"[0-9a-f]{32}|[0-9a-f]{40}")

_SENSITIVE_KEY_RE = re.compile(
    r"token|password|passwd|secret|authorization|cookie|api[_-]?key|session|signature"
    r"|^html$|^text$|^body$|accept_url|invite_url|^dsn$",
    re.IGNORECASE,
)
#: Keys whose values are SDK metadata and are left untouched.
_SAFE_KEYS = frozenset({
    "event_id", "trace_id", "span_id", "parent_span_id", "timestamp", "start_timestamp",
    "level", "platform", "sdk", "release", "environment", "server_name", "modules",
    "logger", "type", "module", "function", "filename", "abs_path", "lineno", "colno",
    "in_app", "mechanism", "handled", "category",
})


def _keep_token(match: "re.Match[str]") -> str:
    value = match.group(0)
    if _UUID_RE.fullmatch(value) or _SAFE_HEX_RE.fullmatch(value):
        return value
    return REDACTED


def scrub_string(value: str) -> str:
    """Redact query values, email local parts and token-like runs in ``value``."""
    value = _QUERY_VALUE_RE.sub(lambda m: m.group(1) + REDACTED, value)
    value = _EMAIL_RE.sub(lambda m: "***@" + m.group(1).lower(), value)
    return _TOKEN_RE.sub(_keep_token, value)


def scrub_query_string(query: Any) -> Any:
    """Keep parameter names, redact every value (str, bytes or pair-list form)."""
    if isinstance(query, bytes):
        query = query.decode("latin-1", "replace")
    if isinstance(query, str):
        pairs = parse_qsl(query.lstrip("?"), keep_blank_values=True)
        return urlencode([(k, REDACTED) for k, _ in pairs], safe="[]")
    if isinstance(query, (list, tuple)):
        return [[scrub_string(str(p[0])), REDACTED] if isinstance(p, (list, tuple)) and p else REDACTED
                for p in query]
    return REDACTED if query else query


def scrub(value: Any, key: Optional[str] = None, depth: int = 0) -> Any:
    """Recursively scrub a JSON-like structure."""
    if key is not None and key in _SAFE_KEYS:
        return value
    if key is not None and _SENSITIVE_KEY_RE.search(key) and value not in (None, "", [], {}):
        return REDACTED
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, bytes):
        return scrub_string(value.decode("utf-8", "replace"))
    if isinstance(value, dict):
        return {k: scrub(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, None, depth + 1) for v in value]
    return value


def _scrub_request(request: Dict[str, Any]) -> Dict[str, Any]:
    request = dict(request)
    if request.get("cookies"):
        request["cookies"] = REDACTED
    if "query_string" in request:
        request["query_string"] = scrub_query_string(request["query_string"])
    if isinstance(request.get("url"), str):
        request["url"] = request["url"].split("?", 1)[0].split("#", 1)[0]
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {
            k: (REDACTED if _SENSITIVE_KEY_RE.search(str(k)) or str(k).lower() in ("referer", "x-forwarded-for")
                else scrub(v, None, 1))
            for k, v in headers.items()
        }
    elif headers:
        request["headers"] = scrub(headers, None, 1)
    if "data" in request:
        request["data"] = scrub(request["data"], None, 1)
    if "env" in request:
        request["env"] = REDACTED
    return {k: (v if k in ("cookies", "query_string", "url", "headers", "data", "env", "method")
                else scrub(v, k, 1)) for k, v in request.items()}


def _scrub_exception_frames(event: Dict[str, Any]) -> None:
    """Drop any captured frame locals (belt and braces with include_local_variables=False)."""
    for container in (event.get("exception") or {}).get("values") or []:
        for frame in ((container or {}).get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame, dict):
                frame.pop("vars", None)
    for thread in (event.get("threads") or {}).get("values") or []:
        for frame in ((thread or {}).get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def scrub_event(event: Dict[str, Any]) -> Dict[str, Any]:
    _scrub_exception_frames(event)
    out: Dict[str, Any] = {}
    for key, value in event.items():
        if key == "request" and isinstance(value, dict):
            out[key] = _scrub_request(value)
        elif key == "user" and isinstance(value, dict):
            # Keep only the opaque id; never email/ip/username.
            out[key] = {"id": value["id"]} if "id" in value else {}
        else:
            out[key] = scrub(value, key)
    return out


def _minimal_event(event: Any) -> Dict[str, Any]:
    """Content-free fallback when full scrubbing fails."""
    minimal: Dict[str, Any] = {"message": "[event content removed: sentry scrubber failed]"}
    if isinstance(event, dict):
        for key in ("event_id", "timestamp", "level", "platform", "release", "environment", "sdk"):
            if key in event:
                minimal[key] = event[key]
        types = []
        try:
            for item in (event.get("exception") or {}).get("values") or []:
                if isinstance(item, dict) and isinstance(item.get("type"), str):
                    types.append({"type": item["type"], "value": REDACTED})
        except Exception:
            types = []
        if types:
            minimal["exception"] = {"values": types}
    return minimal


def before_send(event, hint=None):
    try:
        return scrub_event(event)
    except Exception as exc:
        try:
            logger.warning("Sentry before_send scrubber failed (%s); sending minimal event", type(exc).__name__)
            return _minimal_event(event)
        except Exception:
            logger.warning("Sentry minimal-event fallback failed; event dropped")
            return None


def before_breadcrumb(crumb, hint=None):
    try:
        crumb = dict(crumb)
        data = crumb.get("data")
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("url"), str):
                data["url"] = data["url"].split("?", 1)[0]
            if "http.query" in data:
                data["http.query"] = scrub_query_string(data["http.query"])
            crumb["data"] = data
        return scrub(crumb)
    except Exception as exc:
        try:
            logger.warning("Sentry breadcrumb scrubber failed (%s); breadcrumb dropped", type(exc).__name__)
        except Exception:
            pass
        return None


def sentry_init_options() -> Dict[str, Any]:
    """Privacy-related ``sentry_sdk.init`` options (merged in by ``server.py``)."""
    return {
        "include_local_variables": False,
        "send_default_pii": False,
        "max_request_body_size": "never",
        "before_send": before_send,
        "before_send_transaction": before_send,
        "before_breadcrumb": before_breadcrumb,
    }
