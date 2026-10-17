"""
Common API dependencies — authentication, database access, and RBAC guards.
All authentication is verified against the `public.users` table using FastAPI JWT.
"""
import logging
from typing import Callable, List, Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.security import decode_access_token
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.user import UserOut

logger = logging.getLogger("app.api.deps")
_bearer_scheme = HTTPBearer(auto_error=False)


def get_db():
    """Return Supabase client for database operations."""
    return get_supabase_client()


async def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> UserOut:
    """
    Validate the Bearer JWT token and return the active UserOut from public.users.
    Supports Authorization header or query parameter 'token' (for media preview/downloads).
    Raises:
        HTTP 401 if token is missing, invalid, or expired.
        HTTP 401 if user does not exist in the database.
        HTTP 403 if user account is deactivated.
    """
    token_str: Optional[str] = None
    if creds and creds.credentials:
        token_str = creds.credentials.strip()
    else:
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header:
            auth_header = auth_header.strip()
            if auth_header.lower().startswith("bearer "):
                token_str = auth_header[7:].strip()
            else:
                token_str = auth_header
        elif request.query_params.get("token"):
            token_str = request.query_params.get("token", "").strip()

    if not token_str:
        logger.warning("Auth failure: Missing Authorization header or empty credentials.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if token_str.lower().startswith("bearer "):
        token_str = token_str[7:].strip()

    try:
        payload = decode_access_token(token_str)
    except JWTError as err:
        logger.warning(f"Auth failure: Malformed, expired, or invalid JWT signature. Error: {err}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        logger.warning("Auth failure: Decoded JWT token missing 'sub' user identity.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing user identity.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user existence and active status in DB
    try:
        db = get_supabase_client()
        resp = (
            db.table("users")
            .select("id, name, email, role, is_active, created_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error(f"Auth database lookup failed for user ID '{user_id}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database lookup failed: {exc}",
        )

    if not resp.data:
        logger.warning(f"Auth failure: Authenticated user ID '{user_id}' not found in public.users database.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or credentials invalid.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_row = resp.data[0]
    if not user_row.get("is_active", True):
        logger.warning(f"Auth failure: Account '{user_id}' is deactivated.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated. Contact your administrator.",
        )

    return UserOut(
        id=user_row["id"],
        name=user_row.get("name", ""),
        email=user_row["email"],
        role=RoleEnum(user_row.get("role", "developer")),
        is_active=user_row.get("is_active", True),
        created_at=user_row.get("created_at"),
    )


def require_roles(*allowed_roles: RoleEnum) -> Callable:
    """
    Dependency factory that checks if the authenticated user has one of the allowed roles.
    """
    async def role_checker(current_user: UserOut = Depends(get_current_user)) -> UserOut:
        if current_user.role not in allowed_roles:
            role_names = [r.value for r in allowed_roles]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: action requires one of {role_names} role(s).",
            )
        return current_user

    return role_checker


# Specific role guard dependencies
require_admin = require_roles(RoleEnum.ADMIN)
require_admin_or_manager = require_roles(RoleEnum.ADMIN, RoleEnum.PROJECT_MANAGER)
require_any_authenticated = get_current_user
