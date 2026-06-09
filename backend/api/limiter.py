"""Shared slowapi rate limiter for the API layer.

A single ``Limiter`` instance is created here so the route modules
(``api/routes/chat.py``, ``api/routes/hotels.py``) and the app wiring
(``api/main.py``) all reference the same limiter. Limits are applied to the
PAID endpoints only (/chat, /hotels); /health and other infra routes are left
unlimited (they simply carry no limiter decorator).

Storage is slowapi's in-memory default — correct for the current single-worker
Dockerfile (uvicorn --workers 1). TODO: a multi-instance / multi-worker deploy
needs a shared store (pass ``storage_uri`` → Redis here); not built now.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _client_ip(request: Request) -> str:
    """Rate-limit key: the real client IP, as seen by our trusted proxy.

    The app runs behind exactly one trusted proxy (Railway), so the socket peer
    is the proxy, not the caller. We key off the RIGHT-MOST ``X-Forwarded-For``
    entry: that is the address our single trusted proxy appended — the real
    client as the proxy observed it. The left-most entry is NOT trustworthy:
    it is client-supplied and trivially forgeable, so keying off it would let a
    caller spoof an arbitrary IP and bypass the per-IP limit on the paid
    endpoints. (If we ever sit behind >1 trusted hop, this would need an
    explicit trusted-hop count to pick the right entry instead of the last one.)
    When the header is absent — e.g. local dev with no proxy — fall back to
    slowapi's ``get_remote_address`` (the socket peer).
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        entries = [entry.strip() for entry in forwarded_for.split(",")]
        for client_ip in reversed(entries):
            if client_ip:
                return client_ip
    return get_remote_address(request)


# Single limiter shared across the API layer. No default_limits → routes are
# unlimited unless explicitly decorated, so /health stays open.
limiter = Limiter(key_func=_client_ip)
