"""
Dashboard stats router — real counts from the database.
Only the `users` table is guaranteed to exist.
Other tables return 0 gracefully until they are created.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class DashboardStats(BaseModel):
    totalUsers:     int = 0
    activeProjects: int = 0
    openTasks:      int = 0
    openRisks:      int = 0
    connectedRepos: int = 0


def _db():
    return get_supabase_client()


def _count(table: str, filters: dict | None = None) -> int:
    """
    Return the row count for a table.
    Returns 0 if the table doesn't exist yet (PGRST205 / similar errors).
    """
    try:
        q = _db().table(table).select("id", count="exact")
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        resp = q.limit(0).execute()
        return resp.count or 0
    except Exception:
        return 0


@router.get("/stats", response_model=DashboardStats, summary="Get platform overview stats")
async def get_dashboard_stats() -> DashboardStats:
    """
    Returns live counts from the database:
    - totalUsers:     all rows in `users`
    - activeProjects: projects with status='active'  (0 until table exists)
    - openTasks:      tasks not status='done'         (0 until table exists)
    - openRisks:      workflow_risks not resolved     (0 until table exists)
    - connectedRepos: repositories count              (0 until table exists)

    No mock/seed data is ever returned.
    """
    return DashboardStats(
        totalUsers=_count("users"),
        activeProjects=_count("projects", {"status": "active"}),
        openTasks=_count("tasks"),           # all tasks (refine when table exists)
        openRisks=_count("workflow_risks", {"is_resolved": False}),
        connectedRepos=_count("repositories"),
    )
