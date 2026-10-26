from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.api.deps import get_current_user


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/dashboard/pm-users",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "scheme": "http",
        }
    )


class _Query:
    def __init__(self, execute):
        self._execute = execute

    def table(self, _name):
        return self

    def select(self, _columns):
        return self

    def eq(self, _column, _value):
        return self

    def limit(self, _count):
        return self

    def execute(self):
        return self._execute()


def _windows_socket_error():
    error = OSError("non-blocking socket operation could not be completed immediately")
    error.winerror = 10035
    return error


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: httpx.ConnectError("temporary socket failure"),
        _windows_socket_error,
    ],
)
@pytest.mark.asyncio
async def test_auth_retries_transient_database_transport_error(make_error):
    attempts = 0

    def execute():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise make_error()
        return SimpleNamespace(
            data=[
                {
                    "id": "user-id",
                    "name": "Test User",
                    "email": "test@example.com",
                    "role": "developer",
                    "is_active": True,
                }
            ]
        )

    with (
        patch("app.api.deps.decode_access_token", return_value={"sub": "user-id"}),
        patch("app.api.deps.get_supabase_client", return_value=_Query(execute)),
    ):
        user = await get_current_user(
            _request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token"),
        )

    assert user.id == "user-id"
    assert attempts == 2


@pytest.mark.asyncio
async def test_auth_returns_service_unavailable_after_transport_retries():
    execute = lambda: (_ for _ in ()).throw(httpx.ConnectError("socket unavailable"))

    with (
        patch("app.api.deps.decode_access_token", return_value={"sub": "user-id"}),
        patch("app.api.deps.get_supabase_client", return_value=_Query(execute)),
    ):
        with pytest.raises(HTTPException) as error:
            await get_current_user(
                _request(),
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token"),
            )

    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "1"}
