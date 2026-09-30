"""
Users router — full CRUD against the `users` table using FastAPI JWT auth.

All mutating endpoints for other users (POST / PUT / PATCH / DELETE) require an Admin role.
Any authenticated user can fetch/update their own profile via /users/me.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.deps import get_current_user, require_admin, require_any_authenticated
from app.core.security import hash_password
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.user import (
    UserCreateRequest,
    UserOut,
    UserSelfUpdateRequest,
    UserStatusRequest,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["Users"])


def _db():
    return get_supabase_client()


def _row_to_user_out(row: dict) -> UserOut:
    return UserOut(
        id=row["id"],
        name=row.get("name", ""),
        email=row["email"],
        role=RoleEnum(row.get("role", "developer")),
        is_active=row.get("is_active", True),
        created_at=row.get("created_at"),
    )


# ---------------------------------------------------------------------------
# GET & PATCH /users/me  — current authenticated user profile
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=UserOut,
    summary="Get current user profile",
)
async def get_my_user(
    current_user: UserOut = Depends(get_current_user),
) -> UserOut:
    """Return the profile for the currently authenticated user."""
    return current_user


@router.patch(
    "/me",
    response_model=UserOut,
    summary="Update current user's own profile",
)
async def update_my_user(
    payload: UserSelfUpdateRequest,
    current_user: UserOut = Depends(get_current_user),
) -> UserOut:
    """Allow an authenticated user to update their own display name."""
    if payload.name is None or not payload.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name cannot be blank.",
        )

    clean_name = payload.name.strip()
    try:
        resp = (
            _db()
            .table("users")
            .update({"name": clean_name})
            .eq("id", current_user.id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    return _row_to_user_out(resp.data[0])


# ---------------------------------------------------------------------------
# GET /users  — list all users (any authenticated role)
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=List[UserOut],
    summary="List all users",
)
async def list_users(
    _current_user: UserOut = Depends(require_any_authenticated),
) -> List[UserOut]:
    """Retrieve all registered users from the `users` table."""
    try:
        resp = (
            _db()
            .table("users")
            .select("id, name, email, role, is_active, created_at")
            .order("created_at", desc=True)
            .execute()
        )
        return [_row_to_user_out(r) for r in (resp.data or [])]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {exc}")


# ---------------------------------------------------------------------------
# GET /users/{user_id}  — single user (any authenticated role)
# ---------------------------------------------------------------------------

@router.get(
    "/{user_id}",
    response_model=UserOut,
    summary="Get a single user by ID",
)
async def get_user(
    user_id: str = Path(..., description="The UUID of the user"),
    _current_user: UserOut = Depends(require_any_authenticated),
) -> UserOut:
    """Retrieve a specific user by their UUID."""
    try:
        resp = (
            _db()
            .table("users")
            .select("id, name, email, role, is_active, created_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    return _row_to_user_out(resp.data[0])


# ---------------------------------------------------------------------------
# POST /users  — create user (admin only)
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (admin only)",
)
async def create_user(
    payload: UserCreateRequest,
    _admin: UserOut = Depends(require_admin),
) -> UserOut:
    """
    Admin creates a user directly with a specified role (admin, project_manager, developer).
    Password is hashed server-side; plain text is never stored.
    """
    email = payload.email.lower().strip()

    # Uniqueness check
    try:
        existing = (
            _db()
            .table("users")
            .select("id")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        if existing.data:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    pw_hash = hash_password(payload.password)
    user_id = str(uuid.uuid4())

    try:
        resp = (
            _db()
            .table("users")
            .insert({
                "id": user_id,
                "name": payload.name.strip(),
                "email": email,
                "password_hash": pw_hash,
                "role": payload.role.value,
                "is_active": payload.is_active,
            })
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {exc}")

    if not resp.data:
        raise HTTPException(status_code=500, detail="User creation returned no data.")

    return _row_to_user_out(resp.data[0])


# ---------------------------------------------------------------------------
# PUT /users/{user_id}  — full update (admin only, no password change)
# ---------------------------------------------------------------------------

@router.put(
    "/{user_id}",
    response_model=UserOut,
    summary="Update a user (admin only)",
)
async def update_user_put(
    payload: UserUpdateRequest,
    user_id: str = Path(..., description="The UUID of the user to update"),
    _admin: UserOut = Depends(require_admin),
) -> UserOut:
    """Full update of user fields (name, email, role, is_active). Password not touched."""
    return await _do_update(user_id, payload)


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}  — partial update (admin only)
# ---------------------------------------------------------------------------

@router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="Partially update a user (admin only)",
)
async def update_user_patch(
    payload: UserUpdateRequest,
    user_id: str = Path(..., description="The UUID of the user to update"),
    _admin: UserOut = Depends(require_admin),
) -> UserOut:
    """Partially update name, email, role, or is_active."""
    return await _do_update(user_id, payload)


async def _do_update(user_id: str, payload: UserUpdateRequest) -> UserOut:
    updates: dict = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()
    if payload.email is not None:
        updates["email"] = payload.email.lower().strip()
    if payload.role is not None:
        updates["role"] = payload.role.value
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        resp = (
            _db()
            .table("users")
            .update(updates)
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    return _row_to_user_out(resp.data[0])


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/status  — activate / deactivate (admin only)
# ---------------------------------------------------------------------------

@router.patch(
    "/{user_id}/status",
    response_model=UserOut,
    summary="Activate or deactivate a user (admin only)",
)
async def toggle_user_status(
    body: UserStatusRequest,
    user_id: str = Path(..., description="The UUID of the user"),
    admin_user: UserOut = Depends(require_admin),
) -> UserOut:
    """
    Set `is_active` to true or false.
    An admin cannot deactivate their own account.
    """
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own account status.",
        )

    try:
        resp = (
            _db()
            .table("users")
            .update({"is_active": body.is_active})
            .eq("id", user_id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    return _row_to_user_out(resp.data[0])


# ---------------------------------------------------------------------------
# DELETE /users/{user_id}  — delete user (admin only)
# ---------------------------------------------------------------------------

@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user (admin only)",
)
async def delete_user(
    user_id: str = Path(..., description="The UUID of the user to delete"),
    admin_user: UserOut = Depends(require_admin),
) -> None:
    """
    Permanently delete a user from the database.
    An admin cannot delete their own account.
    """
    if admin_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )

    try:
        _db().table("users").delete().eq("id", user_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {exc}")
