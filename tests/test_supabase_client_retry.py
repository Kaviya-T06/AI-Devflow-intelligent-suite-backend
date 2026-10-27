import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.db.supabase_client import (
    _RetryingHTTPTransport,
    _create_supabase_client,
)
from supabase.lib.client_options import ClientOptions


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/projects/project-id",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "scheme": "http",
        }
    )


def _windows_socket_error() -> OSError:
    error = OSError(
        "non-blocking socket operation could not be completed immediately"
    )
    error.winerror = 10035
    return error


def test_supabase_transport_retries_windows_socket_error():
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(200, request=request)
    inner_transport = Mock()
    inner_transport.handle_request.side_effect = [
        _windows_socket_error(),
        response,
    ]
    transport = _RetryingHTTPTransport(inner_transport)

    with patch("app.db.supabase_client.time.sleep") as sleep:
        result = transport.handle_request(request)

    assert result is response
    assert inner_transport.handle_request.call_count == 2
    sleep.assert_called_once_with(0.1)


def test_supabase_transport_stops_after_retry_limit():
    request = httpx.Request("GET", "https://example.test")
    inner_transport = Mock()
    inner_transport.handle_request.side_effect = _windows_socket_error()
    transport = _RetryingHTTPTransport(inner_transport)

    with (
        patch("app.db.supabase_client.time.sleep"),
        pytest.raises(OSError, match="non-blocking socket"),
    ):
        transport.handle_request(request)

    assert inner_transport.handle_request.call_count == 3


def test_supabase_transport_does_not_retry_non_transient_errors():
    request = httpx.Request("GET", "https://example.test")
    inner_transport = Mock()
    inner_transport.handle_request.side_effect = OSError("permission denied")
    transport = _RetryingHTTPTransport(inner_transport)

    with pytest.raises(OSError, match="permission denied"):
        transport.handle_request(request)

    inner_transport.handle_request.assert_called_once_with(request)


def test_supabase_transport_does_not_retry_windows_error_for_writes():
    request = httpx.Request("POST", "https://example.test")
    inner_transport = Mock()
    inner_transport.handle_request.side_effect = _windows_socket_error()
    transport = _RetryingHTTPTransport(inner_transport)

    with pytest.raises(OSError, match="non-blocking socket"):
        transport.handle_request(request)

    inner_transport.handle_request.assert_called_once_with(request)


def test_supabase_client_uses_retrying_http_client():
    fake_client = SimpleNamespace(postgrest=SimpleNamespace(session=Mock()))
    original_session = fake_client.postgrest.session

    with patch(
        "app.db.supabase_client.create_client",
        return_value=fake_client,
    ) as create_client:
        client = _create_supabase_client("https://example.test", "test-key")

    assert client is fake_client
    if "httpx_client" in inspect.signature(ClientOptions).parameters:
        http_client = create_client.call_args.kwargs["options"].httpx_client
    else:
        http_client = fake_client.postgrest.session
        original_session.close.assert_called_once()

    assert isinstance(http_client._transport, _RetryingHTTPTransport)
    http_client.close()


def test_supabase_rest_requests_use_the_retrying_transport():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[], request=request)

    http_client = httpx.Client(
        transport=_RetryingHTTPTransport(httpx.MockTransport(respond))
    )
    with patch(
        "app.db.supabase_client._create_http_client",
        return_value=http_client,
    ):
        client = _create_supabase_client("https://example.test", "test-key")

    response = client.table("projects").select("*").execute()
    client.postgrest.session.close()

    assert response.data == []
    assert len(requests) == 1
    assert requests[0].url.path == "/rest/v1/projects"
    assert requests[0].headers["apikey"] == "test-key"


@pytest.mark.asyncio
async def test_http_exception_handler_hides_windows_socket_error():
    from main import app

    handler = app.exception_handlers[HTTPException]
    response = await handler(
        _request(),
        HTTPException(
            status_code=500,
            detail=(
                "Database error while retrieving project: "
                "[WinError 10035] A non-blocking socket operation could not be completed immediately"
            ),
        ),
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert b"WinError 10035" not in response.body
