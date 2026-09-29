"""
Activity router — real database queries.
Returns empty list until activity_logs table is created.
"""
from typing import List, Optional

from fastapi import APIRouter, Query

from app.schemas.common import ActivityOut
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/activity", tags=["Activity"])


def _db():
    return get_supabase_client()


@router.get("", response_model=List[ActivityOut], summary="Get recent activity feed")
async def get_activity(
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return"),
) -> List[ActivityOut]:
    """
    Retrieve a chronological feed of recent activity events.
    Returns empty list until the activity_logs table is created in the database.
    """
    try:
        resp = (
            _db()
            .table("activity_logs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
        return [
            ActivityOut(
                id=r["id"],
                actor_id=r.get("actor_id", ""),
                actor_name=r.get("actor_name", ""),
                action=r.get("action", ""),
                resource_type=r.get("resource_type", ""),
                resource_id=r.get("resource_id"),
                resource_name=r.get("resource_name"),
                timestamp=r.get("created_at", r.get("timestamp", "")),
            )
            for r in rows
        ]
    except Exception:
        return []
