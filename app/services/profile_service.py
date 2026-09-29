"""
Profile service — handles database operations for the profiles table.
All writes use the service-role key; reads go through RLS via the anon key.
"""
from uuid import UUID
from typing import Optional

from app.db.supabase_client import get_supabase_client
from app.schemas.profile import ProfileCreate, ProfileUpdate, ProfileRead


class ProfileService:
    """CRUD operations for the profiles table."""

    def __init__(self):
        self._client = get_supabase_client()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_profile(self, user_id: UUID) -> Optional[dict]:
        """Fetch a single profile by user UUID."""
        response = (
            self._client.table("profiles")
            .select("*")
            .eq("id", str(user_id))
            .single()
            .execute()
        )
        return response.data

    def get_all_profiles(self) -> list[dict]:
        """Admin-only: fetch all profiles."""
        response = self._client.table("profiles").select("*").execute()
        return response.data or []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_profile(self, payload: ProfileCreate) -> dict:
        """Insert a new profile row (called after Supabase Auth sign-up)."""
        data = {
            "id": str(payload.id),
            "full_name": payload.full_name,
            "email": payload.email,
            "role": payload.role.value,
            "is_active": payload.is_active,
            "avatar_url": payload.avatar_url,
        }
        response = self._client.table("profiles").insert(data).execute()
        return response.data[0] if response.data else {}

    def update_profile(self, user_id: UUID, payload: ProfileUpdate) -> dict:
        """Update permitted fields of a user's own profile."""
        updates = payload.model_dump(exclude_none=True)
        if not updates:
            return {}
        response = (
            self._client.table("profiles")
            .update(updates)
            .eq("id", str(user_id))
            .execute()
        )
        return response.data[0] if response.data else {}

    def deactivate_profile(self, user_id: UUID) -> dict:
        """Soft-delete: set is_active = False."""
        response = (
            self._client.table("profiles")
            .update({"is_active": False})
            .eq("id", str(user_id))
            .execute()
        )
        return response.data[0] if response.data else {}
