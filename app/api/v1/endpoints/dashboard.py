from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from app.db.supabase_client import get_supabase_client
from app.api.deps import get_current_user
from app.schemas.user import UserOut

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class RecentProject(BaseModel):
    id: str
    name: str
    status: str
    progress: int


class DashboardStats(BaseModel):
    totalUsers:     int = 0
    activeProjects: int = 0
    openTasks:      int = 0
    openRisks:      int = 0
    connectedRepos: int = 0

    # Project-specific detailed metrics
    total_projects: int = 0
    active_projects: int = 0
    completed_projects: int = 0
    on_hold_projects: int = 0
    planning_projects: int = 0
    archived_projects: int = 0
    average_progress: int = 0
    recent_projects: List[RecentProject] = []


def _db():
    return get_supabase_client()


def _count_if_exists(table: str, filters: dict | None = None) -> int:
    """
    Return the row count for a table.
    Returns 0 if the table doesn't exist yet (e.g. for upcoming milestones).
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
async def get_dashboard_stats(current_user: UserOut = Depends(get_current_user)) -> DashboardStats:
    """
    Returns live counts from the database.
    Throws a 500 API error if core queries (users/projects) fail, 
    so the frontend can display an error state.
    """
    role = current_user.role.value.lower() if current_user.role else "developer"
    user_id = current_user.id

    try:
        # Core queries: Do not swallow errors for users or projects
        users_resp = _db().table("users").select("id", count="exact").limit(0).execute()
        total_users = users_resp.count or 0

        projects_query = _db().table("projects").select("id, name, status, progress, updated_at")
        if role == "project_manager":
            projects_query = projects_query.eq("project_manager_id", user_id)
        
        projects_resp = projects_query.order("updated_at", desc=True).execute()
        projects_data = projects_resp.data or []

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error while fetching dashboard stats: {str(exc)}"
        )

    total_projects = 0
    active_projects = 0
    completed_projects = 0
    on_hold_projects = 0
    planning_projects = 0
    archived_projects = 0
    total_progress = 0

    recent_projects = []

    for p in projects_data:
        st = str(p.get("status", "")).lower()
        prog = p.get("progress") or 0

        if st != "archived":
            total_projects += 1
            total_progress += prog

        if st == "active":
            active_projects += 1
        elif st == "completed":
            completed_projects += 1
        elif st == "on_hold":
            on_hold_projects += 1
        elif st == "planning":
            planning_projects += 1
        elif st == "archived":
            archived_projects += 1

    avg_prog = (total_progress // total_projects) if total_projects > 0 else 0

    for p in projects_data[:5]:
        recent_projects.append(RecentProject(
            id=p["id"],
            name=p["name"],
            status=str(p.get("status", "")).lower(),
            progress=p.get("progress") or 0
        ))

    return DashboardStats(
        totalUsers=total_users,
        activeProjects=active_projects,  # Legacy field mapping
        openTasks=_count_if_exists("tasks", {"status": "open"}),
        openRisks=_count_if_exists("workflow_risks", {"is_resolved": False}),
        connectedRepos=_count_if_exists("repositories"),
        
        total_projects=total_projects,
        active_projects=active_projects,
        completed_projects=completed_projects,
        on_hold_projects=on_hold_projects,
        planning_projects=planning_projects,
        archived_projects=archived_projects,
        average_progress=avg_prog,
        recent_projects=recent_projects
    )
