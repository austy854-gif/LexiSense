"""Trust ``X-Forwarded-Proto`` from the trusted proxy hop only.

uvicorn runs with ``--forwarded-allow-ips 127.0.0.1`` (it must not rewrite the
client address from a spoofable X-Forwarded-For), so it no longer applies
X-Forwarded-Proto either and redirects (e.g. redirect_slashes 307s) would point
at http://. This middleware sets ``scope["scheme"]`` from the entry
``FORWARDED_PROTO_HOP`` hops from the right (default: ``TRUSTED_PROXY_COUNT``),
accepting only http/https. Many load balancers/CDNs *overwrite*
X-Forwarded-Proto instead of appending, so with a CDN + LB set
``FORWARDED_PROTO_HOP=1`` even though ``TRUSTED_PROXY_COUNT=2``.
"""
import os

from utils.rbac import _trusted_proxy_count

_ALLOWED = {"http", "https"}


def _forwarded_proto_hop() -> int:
    raw = os.environ.get("FORWARDED_PROTO_HOP")
    if raw is None or raw.strip() == "":
        return _trusted_proxy_count()
    try:
        value = int(raw)
    except ValueError:
        return _trusted_proxy_count()
    if value < 0:
        return _trusted_proxy_count()
    return value


class ForwardedProtoMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            hops = _forwarded_proto_hop()
            if hops:
                values = [v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-forwarded-proto"]
                parts = [p.strip().lower() for v in values for p in v.split(",") if p.strip()]
                if len(parts) >= hops and parts[-hops] in _ALLOWED:
                    proto = parts[-hops]
                    if scope["type"] == "websocket":
                        proto = "wss" if proto == "https" else "ws"
                    scope = dict(scope, scheme=proto)
        await self.app(scope, receive, send)
