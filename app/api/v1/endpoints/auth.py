"""
Authentication router — register and login backed by the `users` table.
No Supabase Auth. No profiles table. No user_credentials table.
"""
from fastapi import APIRouter

from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.services.auth_service import register_user, login_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    summary="Register a new user",
    status_code=201,
)
async def register(payload: RegisterRequest) -> RegisterResponse:
    """
    Register a new user account.

    - Checks email uniqueness in the `users` table.
    - Hashes the password with **bcrypt** (never stored in plain text).
    - Inserts one row into `users` (id, name, email, password_hash, role, is_active).
    - Returns the created user — password hash is **never** exposed.

    **Supported roles:** `admin` · `developer` · `project_manager`

    ```json
    {
      "name": "Jane Smith",
      "email": "jane@example.com",
      "password": "secret123",
      "role": "developer"
    }
    ```
    """
    return register_user(payload)


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Login with email and password",
)
async def login(payload: LoginRequest) -> LoginResponse:
    """
    Authenticate a user with **email** and **password**.

    - Looks up the user by email in the `users` table.
    - Verifies the entered password against `users.password_hash` (bcrypt).
    - Checks `users.is_active`.
    - Issues a signed **JWT** access token (HS256, valid 24 h).
    - Returns the token, token type, and authenticated user with their role.

    ```json
    { "email": "jane@example.com", "password": "secret123" }
    ```
    """
    return login_user(payload)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Logout the current user",
)
async def logout() -> LogoutResponse:
    """
    Logout the current user.

    The client must discard the JWT. Server-side token blacklisting
    can be added in a future milestone if needed.
    """
    return LogoutResponse(message="Logged out successfully.")
