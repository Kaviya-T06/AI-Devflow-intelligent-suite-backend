"""
Profiles router — CRUD operations for user profiles.
Authentication is validated by checking the JWT token issued by Supabase Auth.
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Header, Depends
from typing import Optional

from app.schemas.profile import ProfileRead, ProfileUpdate
from app.services.profile_service import ProfileService
from app.db.supabase_client import get_supabase_anon_client

router = APIRouter(prefix="/profiles", tags=["Profiles"])


def _verify_token(authorization: Optional[str] = Header(None)) -> dict:
    """
    Validate the Bearer JWT token using Supabase anon client.
    Returns the user payload if valid; raises 401 otherwise.
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


@router.get("/me", response_model=ProfileRead, summary="Get own profile")
async def get_my_profile(auth: dict = Depends(_verify_token)):
    """Return the authenticated user's profile."""
    user = auth["user"]
    service = ProfileService()
    profile = service.get_profile(UUID(user.id))
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.patch("/me", response_model=ProfileRead, summary="Update own profile")
async def update_my_profile(
    payload: ProfileUpdate,
    auth: dict = Depends(_verify_token),
):
    """Update the authenticated user's permitted profile fields."""
    user = auth["user"]
    service = ProfileService()
    updated = service.update_profile(UUID(user.id), payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Profile not found or no fields to update")
    return updated


@router.get(
    "",
    response_model=list[ProfileRead],
    summary="List all profiles (Admin only)",
)
async def list_profiles(auth: dict = Depends(_verify_token)):
    """
    Return all user profiles.
    In Milestone 1 this is open to any authenticated user for development purposes.
    In production, add an admin-role guard here.
    """
    service = ProfileService()
    return service.get_all_profiles()
