"""
LEGACY MODULE — Supabase Auth Profiles Router

ARCHITECTURAL CONFLICT & ISOLATION NOTE:
This router originally validated tokens using Supabase GoTrue Auth (via `supabase.auth.get_user(token)`)
and read/wrote from a separate `profiles` table.
The application's active authentication architecture is FastAPI JWT backed by `public.users`.

For all current application flows, user profiles and permissions are managed through:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/register`
  - `GET /api/v1/users/me`
  - `PATCH /api/v1/users/me`
  - `GET /api/v1/users` (CRUD)

This module is isolated from the active authentication pipeline and preserved without silent deletion.
"""
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Depends

from app.schemas.profile import ProfileRead, ProfileUpdate
from app.services.profile_service import ProfileService
from app.db.supabase_client import get_supabase_anon_client

router = APIRouter(prefix="/profiles", tags=["Profiles (Legacy Supabase Auth)"])


def _verify_token(authorization: Optional[str] = Header(None)) -> dict:
    """
    Legacy Supabase Auth token validator.
    Validates token against Supabase GoTrue auth client.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        client = get_supabase_anon_client()
        user_response = client.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return {"user": user_response.user}
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(exc)}")


@router.get("/me", response_model=ProfileRead, summary="[Legacy] Get own profile via Supabase Auth")
async def get_my_profile(auth: dict = Depends(_verify_token)):
    """Return the Supabase Auth user's profile."""
    user = auth["user"]
    service = ProfileService()
    profile = service.get_profile(UUID(user.id))
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.patch("/me", response_model=ProfileRead, summary="[Legacy] Update own profile via Supabase Auth")
async def update_my_profile(
    payload: ProfileUpdate,
    auth: dict = Depends(_verify_token),
):
    """Update the Supabase Auth user's permitted profile fields."""
    user = auth["user"]
    service = ProfileService()
    updated = service.update_profile(UUID(user.id), payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Profile not found or no fields to update")
    return updated


@router.get(
    "",
    response_model=list[ProfileRead],
    summary="[Legacy] List all profiles via Supabase Auth",
)
async def list_profiles(auth: dict = Depends(_verify_token)):
    """Return all Supabase Auth user profiles."""
    service = ProfileService()
    return service.get_all_profiles()
