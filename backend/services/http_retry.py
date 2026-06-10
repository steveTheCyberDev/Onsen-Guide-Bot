"""Shared outbound HTTP retry helper for the services layer.

Wraps ``requests.get`` with tenacity so transient failures are retried a few
times with jittered exponential backoff, while NON-transient failures pass
straight through unchanged. This keeps each service's existing return/exception
shapes intact (graceful degradation): the helper only retries, it never alters
the response a caller eventually sees.

LangChain-agnostic on purpose — pure ``requests`` + ``tenacity``.

What counts as transient (and is retried):
  * connection errors and timeouts (``requests.exceptions.ConnectionError`` /
    ``Timeout``) — the request never got a usable response.
  * HTTP 5xx responses — the server failed; a retry may succeed.

What is NOT retried (returned to the caller on the first attempt):
  * HTTP 4xx (client errors) — bad params/credentials won't fix themselves.
  * any 2xx/3xx — success path.
"""

import logging

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)

# Retry tuning. 3 total attempts with jittered exponential backoff, capped so a
# retrying request can't stall a user-facing call for long.
_MAX_ATTEMPTS = 3
_BACKOFF_INITIAL_SECONDS = 0.5  # first wait before jitter
_BACKOFF_MAX_SECONDS = 4  # cap on any single wait (a few seconds, per spec)


class _TransientHTTPError(Exception):
    """Internal marker: a 5xx response worth retrying.

    Never escapes this module — the final attempt returns the underlying
    response instead of raising, so callers only ever see a real Response or one
    of requests' own exceptions, exactly as before.
    """

    def __init__(self, response: requests.Response) -> None:
        self.response = response
        super().__init__(f"transient upstream status {response.status_code}")


@retry(
    stop=stop_after_attempt(_MAX_ATTEMPTS),
    wait=wait_exponential_jitter(
        initial=_BACKOFF_INITIAL_SECONDS, max=_BACKOFF_MAX_SECONDS
    ),
    # Retry only on transient connection/timeout errors and our 5xx marker.
    # 4xx never raises the marker, so it is returned without a retry.
    retry=retry_if_exception_type(
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            _TransientHTTPError,
        )
    ),
    reraise=True,
)
def _get_retrying(url: str, **kwargs) -> requests.Response:
    response = requests.get(url, **kwargs)
    if response.status_code >= 500:
        logger.warning(
            "transient upstream %s from %s — retrying", response.status_code, url
        )
        raise _TransientHTTPError(response)
    return response


def get_with_retries(url: str, **kwargs) -> requests.Response:
    """``requests.get`` with bounded retries on transient failures.

    Accepts and forwards the same kwargs as ``requests.get`` (``params``,
    ``headers``, ``timeout``, ...), so callers keep their existing per-request
    timeout. Returns the ``requests.Response`` for any non-5xx outcome. On
    repeated 5xx responses, returns the last 5xx response (caller's existing
    error handling then runs). On repeated connection/timeout errors, re-raises
    the last ``requests`` exception (same as a non-retried call would).
    """
    try:
        return _get_retrying(url, **kwargs)
    except _TransientHTTPError as exc:
        # Exhausted retries on 5xx — hand the response back so the caller's
        # existing status/body handling stays in charge (no new crash).
        return exc.response
