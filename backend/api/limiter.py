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
    """Rate-limit key: the real client IP.

    The app runs behind Railway's proxy, so the socket peer is the proxy, not
    the caller. The real client IP is the left-most entry of ``X-Forwarded-For``
    (the original client; later entries are intermediate proxies). When the
    header is absent — e.g. local dev with no proxy — fall back to slowapi's
    ``get_remote_address`` (the socket peer). We only read the left-most XFF
    entry rather than trusting the whole chain.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    return get_remote_address(request)


# Single limiter shared across the API layer. No default_limits → routes are
# unlimited unless explicitly decorated, so /health stays open.
limiter = Limiter(key_func=_client_ip)
