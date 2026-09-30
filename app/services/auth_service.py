"""
Auth service — uses ONLY the `users` table.

No profiles table. No user_credentials table. No Supabase Auth.

Register flow:
    1. Check email uniqueness in `users`.
    2. Hash password with bcrypt.
    3. INSERT into `users` (id, name, email, password_hash, role, is_active).
    4. Return user info (no hash exposed).

Login flow:
    1. SELECT from `users` by email.
    2. Verify bcrypt password against users.password_hash.
    3. Check users.is_active.
    4. Get role from users.role.
    5. Issue signed JWT.
    6. Return token + user info.

Supported roles (stored as-is in DB): admin | developer | project_manager
"""
import uuid
from typing import Optional

from fastapi import HTTPException, status

from app.core.security import hash_password, verify_password, create_access_token
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import (
    RegisterRequest, LoginRequest,
    LoginResponse, RegisterResponse,
    AuthUserOut, RoleEnum,
)

# ---------------------------------------------------------------------------
# Internal DB helper
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_client()


def _get_user_by_email(email: str) -> Optional[dict]:
    """
    SELECT id, name, email, password_hash, role, is_active
    FROM users WHERE email = :email LIMIT 1.
    Returns None if not found or on error.
    """
    try:
        r = (
            _db()
            .table("users")
            .select("id, name, email, password_hash, role, is_active")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception as exc:
        msg = str(exc)
        if "PGRST205" in msg or "'users'" in msg:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Database migration required: run "
                    "migrations/create_users_table.sql in the Supabase SQL Editor."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {msg}",
        )


# ---------------------------------------------------------------------------
# Register  →  POST /api/v1/auth/register
# ---------------------------------------------------------------------------

def register_user(payload: RegisterRequest) -> RegisterResponse:
    """
    Creates one row in `users`. No profiles table involved.
    Returns HTTP 409 if email already registered.
    """
    email = payload.email.lower().strip()

    # ── 1. Uniqueness check ────────────────────────────────────────────────
    if _get_user_by_email(email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # ── 2. Hash password ───────────────────────────────────────────────────
    pw_hash = hash_password(payload.password)

    # ── 3. Insert into users ───────────────────────────────────────────────
    # Public registration unconditionally defaults to 'developer' role.
    # Elevated roles (admin / project_manager) must be created/assigned by an authorized admin.
    safe_role = RoleEnum.DEVELOPER.value

    user_id = str(uuid.uuid4())
    try:
        resp = _db().table("users").insert({
            "id":            user_id,
            "name":          payload.name.strip(),
            "email":         email,
            "password_hash": pw_hash,
            "role":          safe_role,
            "is_active":     True,
        }).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user: {exc}",
        )

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="User creation returned no data.",
        )

    created = resp.data[0]

    return RegisterResponse(
        message="User registered successfully.",
        user=AuthUserOut(
            id=created["id"],
            name=created["name"],
            email=created["email"],
            role=RoleEnum(created["role"]),
        ),
    )


# ---------------------------------------------------------------------------
# Login  →  POST /api/v1/auth/login
# ---------------------------------------------------------------------------

def login_user(payload: LoginRequest) -> LoginResponse:
    """
    Authenticates against `users` only.
    Returns a signed JWT on success; HTTP 401 on any failure.
    """
    email = payload.email.lower().strip()

    _INVALID = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid login credentials",
    )

    # ── 1. Find user ───────────────────────────────────────────────────────
    user = _get_user_by_email(email)
    if not user:
        raise _INVALID

    # ── 2. Verify password ─────────────────────────────────────────────────
    if not verify_password(payload.password, user["password_hash"]):
        raise _INVALID

    # ── 3. Active check ────────────────────────────────────────────────────
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated. Contact your administrator.",
        )

    # ── 4. Issue JWT ───────────────────────────────────────────────────────
    role_str = user["role"]   # e.g. "admin", "developer", "project_manager"
    token = create_access_token({
        "sub":   user["id"],
        "email": user["email"],
        "role":  role_str,
    })

    return LoginResponse(
        message="Login successful.",
        access_token=token,
        token_type="bearer",
        user=AuthUserOut(
            id=user["id"],
            name=user["name"],
            email=user["email"],
            role=RoleEnum(role_str),
        ),
    )
