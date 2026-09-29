"""
Users router — real database queries against the `users` table.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, status

from app.schemas.auth import RoleEnum
from app.schemas.user import UserOut, UserUpdateRequest
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/users", tags=["Users"])


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_client()


def _row_to_user_out(row: dict) -> UserOut:
    return UserOut(
        id=row["id"],
        name=row.get("name", ""),
        email=row["email"],
        role=RoleEnum(row.get("role", "developer")),
        is_active=row.get("is_active", True),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=List[UserOut],
    summary="List all users",
)
async def list_users() -> List[UserOut]:
    """
    Retrieve all registered users from the `users` table.
    Returns id, name, email, role, and is_active for each user.
    """
    try:
        resp = _db().table("users").select(
            "id, name, email, role, is_active"
        ).execute()
        return [_row_to_user_out(r) for r in (resp.data or [])]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch users: {exc}",
        )


@router.get(
    "/{user_id}",
    response_model=UserOut,
    summary="Get a single user by ID",
)
async def get_user(
    user_id: str = Path(..., description="The UUID of the user"),
) -> UserOut:
    """Retrieve a specific user by their UUID."""
    try:
        resp = (
            _db()
            .table("users")
            .select("id, name, email, role, is_active")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not resp.data:
        raise HTTPException(status_code=404, detail="User not found.")
    return _row_to_user_out(resp.data[0])


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="Update a user's details",
)
async def update_user(
    payload: UserUpdateRequest,
    user_id: str = Path(..., description="The UUID of the user to update"),
) -> UserOut:
    """Partially update a user's name, role, or is_active flag."""
    updates: dict = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()
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
