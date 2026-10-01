"""
Activity router — real database queries.
Returns empty list until activity_logs table is created.
"""
from typing import List, Optional

from app.schemas.common import ActivityOut
from app.db.supabase_client import get_supabase_client
from app.api.deps import get_current_user
from app.schemas.user import UserOut
from app.schemas.auth import RoleEnum
from fastapi import APIRouter, Query, Depends

router = APIRouter(prefix="/activity", tags=["Activity"])


def _db():
    return get_supabase_client()


@router.get("", response_model=List[ActivityOut], summary="Get recent activity feed")
async def get_activity(
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return"),
    current_user: UserOut = Depends(get_current_user),
) -> List[ActivityOut]:
    """
    Retrieve a chronological feed of recent activity events.
    Developers only see their own activity.
    """
    try:
        # Join users table to get user info
        query = _db().table("activity_logs").select("*, users(id, name, email)")
        
        if current_user.role == RoleEnum.DEVELOPER:
            query = query.eq("user_id", current_user.id)
            
        resp = query.order("created_at", desc=True).limit(limit).execute()
        rows = resp.data or []
        
        results = []
        for r in rows:
            user_data = None
            if r.get("users"):
                user_data = {
                    "id": r["users"].get("id", ""),
                    "full_name": r["users"].get("name", ""),
                    "email": r["users"].get("email", ""),
                }

            results.append(ActivityOut(
                id=str(r["id"]),
                user_id=r.get("user_id"),
                action=r.get("action", ""),
                entity_type=r.get("entity_type", ""),
                entity_id=r.get("entity_id"),
                description=r.get("description", ""),
                created_at=r.get("created_at", ""),
                user=user_data
            ))
        return results
    except Exception as exc:
        print(f"Error fetching activity: {exc}")
        return []
