"""Shared FastAPI dependencies.

`require_api_key` guards the paid endpoints (/chat, /hotels) behind a static
shared secret sent in the ``X-API-Key`` header. /health is intentionally left
open for Railway healthchecks.
"""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from core.config import settings

API_KEY_HEADER_NAME = "X-API-Key"

# auto_error=False so a *missing* header reaches our handler and gets a uniform
# 401 (FastAPI's default would be a 403). We return the same 401 for both a
# missing and a wrong key so callers can't distinguish the two.
_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def require_api_key(provided_key: str | None = Depends(_api_key_header)) -> None:
    """Reject the request unless a valid X-API-Key header is present.

    Fails closed: if settings.api_key is empty (unset in the environment) no key
    can ever match, so every guarded request is rejected.
    """
    expected = settings.api_key
    if (
        not expected
        or not provided_key
        or not secrets.compare_digest(provided_key, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
