"""Trust ``X-Forwarded-Proto`` from the trusted proxy hop only.

uvicorn runs with ``--forwarded-allow-ips 127.0.0.1`` (it must not rewrite the
client address from a spoofable X-Forwarded-For), so it no longer applies
X-Forwarded-Proto either and redirects (e.g. redirect_slashes 307s) would point
at http://. This middleware sets ``scope["scheme"]`` from the entry
``TRUSTED_PROXY_COUNT`` hops from the right, accepting only http/https.
"""
from utils.rbac import _trusted_proxy_count

_ALLOWED = {"http", "https"}


class ForwardedProtoMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            hops = _trusted_proxy_count()
            if hops:
                values = [v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-forwarded-proto"]
                parts = [p.strip().lower() for v in values for p in v.split(",") if p.strip()]
                if len(parts) >= hops and parts[-hops] in _ALLOWED:
                    proto = parts[-hops]
                    if scope["type"] == "websocket":
                        proto = "wss" if proto == "https" else "ws"
                    scope = dict(scope, scheme=proto)
        await self.app(scope, receive, send)
