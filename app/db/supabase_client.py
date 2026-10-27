"""
Supabase client factory — used by backend services.
Uses the SERVICE ROLE key for admin operations.
Never expose this key to the frontend.
"""
import inspect
from functools import lru_cache
import logging
import time

import httpx
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

from app.core.config import settings

logger = logging.getLogger(__name__)
_DATABASE_REQUEST_ATTEMPTS = 3
_DATABASE_RETRY_DELAY_SECONDS = 0.1


def is_windows_nonblocking_socket_error(exc: BaseException) -> bool:
    """Identify WinError 10035, including errors wrapped by service layers."""
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and getattr(current, "winerror", None) == 10035:
            return True
        current = current.__cause__ or current.__context__
    detail = str(getattr(exc, "detail", exc)).lower()
    return (
        "winerror 10035" in detail
        or "winerror: 10035" in detail
        or "non-blocking socket operation could not be completed immediately" in detail
    )


class _RetryingHTTPTransport(httpx.BaseTransport):
    """Retry transient connection failures for every Supabase REST operation."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport or httpx.HTTPTransport(http2=True)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(_DATABASE_REQUEST_ATTEMPTS):
            try:
                return self._transport.handle_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as exc:
                retryable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                if (
                    not retryable
                    and request.method in {"GET", "HEAD", "OPTIONS"}
                    and is_windows_nonblocking_socket_error(exc)
                ):
                    retryable = True
                if not retryable or attempt + 1 == _DATABASE_REQUEST_ATTEMPTS:
                    raise

                logger.warning(
                    "Transient Supabase connection failure (attempt %s/%s): %s",
                    attempt + 1,
                    _DATABASE_REQUEST_ATTEMPTS,
                    exc,
                )
                time.sleep(_DATABASE_RETRY_DELAY_SECONDS * (attempt + 1))

        raise RuntimeError("Supabase connection retry loop ended unexpectedly.")

    def close(self) -> None:
        self._transport.close()


def _create_http_client() -> httpx.Client:
    return httpx.Client(
        transport=_RetryingHTTPTransport(),
        timeout=120,
        follow_redirects=True,
        http2=True,
    )


def _create_supabase_client(url: str, key: str) -> Client:
    http_client = _create_http_client()
    if "httpx_client" in inspect.signature(ClientOptions).parameters:
        options = ClientOptions(httpx_client=http_client)
        return create_client(url, key, options=options)

    # Older Supabase releases do not accept a custom HTTP client in ClientOptions.
    client = create_client(url, key)
    postgrest = client.postgrest
    postgrest.session.close()
    postgrest.session = http_client
    return client


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a cached Supabase client using the service-role key."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
        )
    return _create_supabase_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
    )


@lru_cache(maxsize=1)
def get_supabase_anon_client() -> Client:
    """Return a cached Supabase client using the anon key (public operations)."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env"
        )
    return _create_supabase_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_ANON_KEY,
    )
