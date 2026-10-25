"""
Auth service — uses ONLY the `users` table.

No profiles table. No user_credentials table. No Supabase Auth.

Register flow:
    1. Check email uniqueness in `users`.
    2. Hash password with bcrypt.
    3. Determine authoritative role:
       - Default public registration assigns 'developer'.
       - Privileged roles ('admin', 'project_manager') require an authorized
         invitation/approval code.
       - Unauthorized privileged role attempts with no code safely fall back
         to 'developer' (preventing privilege escalation).
       - Invalid invite codes return HTTP 403 Forbidden.
    4. INSERT into `users` (id, name, email, password_hash, role, is_active).
    5. Return user info (no hash exposed).

Login flow:
    1. SELECT from `users` by email.
    2. Verify bcrypt password against users.password_hash.
    3. Check users.is_active.
    4. Get authoritative role from users.role.
    5. Issue signed JWT with sub, email, and authoritative role claims.
    6. Return token + user info.

Supported roles (stored as-is in DB): admin | developer | project_manager
"""
import uuid
from typing import Optional

from fastapi import HTTPException, status

from app.core.config import settings
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
    Creates one row in `users`.
    Authoritative role resolution:
      - Default / Developer: safe default for public registration.
      - Admin / Project Manager: requires valid invitation/approval code.
      - Attempts to self-assign privileged roles without code safely fall back to Developer.
      - Invalid invite code returns HTTP 403 Forbidden.
    Returns HTTP 409 if email already registered.
    """
    email = payload.email.lower().strip()
    user_name = (payload.name or payload.full_name or "").strip()
    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name cannot be blank.",
        )

    # ── 1. Uniqueness check ────────────────────────────────────────────────
    if _get_user_by_email(email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # ── 2. Hash password ───────────────────────────────────────────────────
    pw_hash = hash_password(payload.password)

    # ── 3. Role resolution & security enforcement ──────────────────────────
    requested_role = payload.role if payload.role else RoleEnum.DEVELOPER
    assigned_role = RoleEnum.DEVELOPER.value

    if requested_role in (RoleEnum.ADMIN, RoleEnum.PROJECT_MANAGER):
        submitted_code = (payload.invite_code or payload.inviteCode or "").strip()
        if submitted_code:
            expected_admin_code = getattr(settings, "ADMIN_INVITE_CODE", "DEVFLOW-ADMIN-SECURE-KEY")
            expected_pm_code = getattr(settings, "MANAGER_INVITE_CODE", "DEVFLOW-PM-SECURE-KEY")
            expected_general_code = getattr(settings, "INVITATION_CODE", "")

            is_authorized = False
            if requested_role == RoleEnum.ADMIN:
                if submitted_code and submitted_code in (expected_admin_code, expected_general_code):
                    is_authorized = True
            elif requested_role == RoleEnum.PROJECT_MANAGER:
                if submitted_code and submitted_code in (expected_pm_code, expected_admin_code, expected_general_code):
                    is_authorized = True

            if is_authorized:
                assigned_role = requested_role.value
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid invitation or authorization code for privileged role.",
                )
        else:
            # Public registration without authorization code safely defaults to Developer
            assigned_role = RoleEnum.DEVELOPER.value
    else:
        assigned_role = RoleEnum.DEVELOPER.value

    user_id = str(uuid.uuid4())
    try:
        resp = _db().table("users").insert({
            "id":            user_id,
            "name":          user_name,
            "email":         email,
            "password_hash": pw_hash,
            "role":          assigned_role,
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
    Reads authoritative role directly from users.role.
    Returns a signed JWT with role claim on success; HTTP 401 on any failure.
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

    # ── 4. Issue JWT with authoritative database role ──────────────────────
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
