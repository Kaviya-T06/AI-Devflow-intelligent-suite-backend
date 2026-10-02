"""
Dashboard router — role-aware stats for Admin, Project Manager, and Developer roles.
Project Manager gets PM-specific dashboard with team task monitoring & overdue tracking.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone

from app.db.supabase_client import get_supabase_client
from app.api.deps import get_current_user
from app.schemas.user import UserOut
from app.schemas.auth import RoleEnum

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ---------------------------------------------------------------------------
# Shared models
# ---------------------------------------------------------------------------

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

    # Risk Metrics Breakdown
    high_risks: int = 0
    medium_risks: int = 0
    low_risks: int = 0
    overdue_tasks_risks: int = 0
    review_delay_risks: int = 0
    stuck_tasks_risks: int = 0
    project_delay_risks: int = 0


# ---------------------------------------------------------------------------
# PM-specific models
# ---------------------------------------------------------------------------

class DeveloperWorkload(BaseModel):
    developer_id: str
    developer_name: str
    total_tasks: int = 0
    completed_tasks: int = 0
    in_progress_tasks: int = 0
    review_tasks: int = 0
    todo_tasks: int = 0
    overdue_tasks: int = 0


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: str
    progress: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_tasks: int = 0
    completed_tasks: int = 0
    in_progress_tasks: int = 0
    review_tasks: int = 0
    todo_tasks: int = 0
    overdue_tasks: int = 0
    developers: List[DeveloperWorkload] = []


class OverdueTaskSummary(BaseModel):
    task_id: str
    task_title: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    assigned_to: Optional[str] = None
    developer_name: Optional[str] = None
    due_date: str
    status: str
    priority: str


class PMDashboardStats(BaseModel):
    total_projects: int = 0
    active_projects: int = 0
    planning_projects: int = 0
    on_hold_projects: int = 0
    completed_projects: int = 0
    total_tasks: int = 0
    completed_tasks: int = 0
    in_progress_tasks: int = 0
    review_tasks: int = 0
    todo_tasks: int = 0
    overdue_tasks: int = 0
    average_progress: int = 0
    projects: List[ProjectSummary] = []
    overdue_task_list: List[OverdueTaskSummary] = []


class ActivityItem(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    description: str
    created_at: str
    user_name: Optional[str] = None
    user_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _db():
    return get_supabase_client()


def _count_if_exists(table: str, filters: dict | None = None) -> int:
    """Return the row count for a table; returns 0 if table doesn't exist."""
    try:
        q = _db().table(table).select("id", count="exact")
        if filters:
            for col, val in filters.items():
                q = q.eq(col, val)
        resp = q.limit(0).execute()
        return resp.count or 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# GET /dashboard/stats  — Admin / Developer stats (existing)
# ---------------------------------------------------------------------------

@router.get("/stats", response_model=DashboardStats, summary="Get platform overview stats")
async def get_dashboard_stats(current_user: UserOut = Depends(get_current_user)) -> DashboardStats:
    """
    Returns live counts from the database.
    - Admin: full platform stats.
    - Project Manager: use /dashboard/pm-stats instead.
    - Developer: scoped to their project tasks.
    Raises 500 for core DB failures.
    """
    role = current_user.role.value.lower() if current_user.role else "developer"
    user_id = current_user.id

    try:
        users_resp = _db().table("users").select("id", count="exact").limit(0).execute()
        total_users = users_resp.count or 0

        projects_query = _db().table("projects").select("id, name, status, progress, updated_at")
        if role == "project_manager":
            projects_query = projects_query.eq("project_manager_id", user_id)
        elif role == "developer":
            task_resp = _db().table("tasks").select("project_id").eq("assigned_to", user_id).execute()
            project_ids = list(set([str(t["project_id"]) for t in (task_resp.data or []) if t.get("project_id")]))
            if project_ids:
                projects_query = projects_query.in_("id", project_ids)
            else:
                projects_query = projects_query.in_("id", ["00000000-0000-0000-0000-000000000000"])

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

    # Fetch workflow risks to compute breakdown
    try:
        from app.services.workflow_risk_service import detect_and_update_risks
        detect_and_update_risks()
        
        risks_query = _db().table("workflow_risks").select("level, risk_type").eq("is_resolved", False)
        if role == "project_manager":
            if projects_data:
                proj_ids = [str(p["id"]) for p in projects_data]
                risks_query = risks_query.in_("project_id", proj_ids)
        elif role == "developer":
            risks_query = risks_query.eq("user_id", user_id)
            
        risks_resp = risks_query.execute()
        open_risks_data = risks_resp.data or []
    except Exception as exc:
        print(f"Error fetching risks for dashboard: {exc}")
        open_risks_data = []

    high_risks = sum(1 for r in open_risks_data if str(r.get("level", "")).lower() == "high")
    medium_risks = sum(1 for r in open_risks_data if str(r.get("level", "")).lower() == "medium")
    low_risks = sum(1 for r in open_risks_data if str(r.get("level", "")).lower() == "low")
    
    overdue_tasks_risks = sum(1 for r in open_risks_data if r.get("risk_type") == "OVERDUE_TASK")
    review_delay_risks = sum(1 for r in open_risks_data if r.get("risk_type") == "REVIEW_DELAY")
    stuck_tasks_risks = sum(1 for r in open_risks_data if r.get("risk_type") == "STUCK_TASK")
    project_delay_risks = sum(1 for r in open_risks_data if r.get("risk_type") == "PROJECT_DELAY")

    return DashboardStats(
        totalUsers=total_users,
        activeProjects=active_projects,
        openTasks=_count_if_exists("tasks", {"status": "TODO"}) + _count_if_exists("tasks", {"status": "IN_PROGRESS"}), # using TODO + IN_PROGRESS 
        openRisks=len(open_risks_data),
        connectedRepos=_count_if_exists("repositories"),

        total_projects=total_projects,
        active_projects=active_projects,
        completed_projects=completed_projects,
        on_hold_projects=on_hold_projects,
        planning_projects=planning_projects,
        archived_projects=archived_projects,
        average_progress=avg_prog,
        recent_projects=recent_projects,
        
        high_risks=high_risks,
        medium_risks=medium_risks,
        low_risks=low_risks,
        overdue_tasks_risks=overdue_tasks_risks,
        review_delay_risks=review_delay_risks,
        stuck_tasks_risks=stuck_tasks_risks,
        project_delay_risks=project_delay_risks
    )


# ---------------------------------------------------------------------------
# GET /dashboard/pm-stats  — Project Manager dashboard stats
# ---------------------------------------------------------------------------

@router.get(
    "/pm-stats",
    response_model=PMDashboardStats,
    summary="Get Project Manager dashboard statistics",
)
async def get_pm_dashboard_stats(
    current_user: UserOut = Depends(get_current_user),
) -> PMDashboardStats:
    """
    Returns comprehensive PM dashboard data:
    - Only projects where current_user is the project_manager.
    - Per-project task breakdown (total, completed, in-progress, review, todo, overdue).
    - Per-developer workload within each project.
    - Overdue task list (due_date < now AND status != COMPLETED).
    
    Restricted to PROJECT_MANAGER and ADMIN roles.
    """
    role = current_user.role.value if current_user.role else ""
    if role not in (RoleEnum.PROJECT_MANAGER.value, RoleEnum.ADMIN.value):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: only Project Managers or Admins can access PM dashboard stats.",
        )

    pm_id = current_user.id

    # Fetch managed projects
    try:
        proj_query = _db().table("projects").select("*")
        if role == RoleEnum.PROJECT_MANAGER.value:
            proj_query = proj_query.eq("project_manager_id", pm_id)
        projects_resp = proj_query.order("created_at", desc=True).execute()
        projects_data = projects_resp.data or []
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching projects: {str(exc)}",
        )

    if not projects_data:
        return PMDashboardStats()

    project_ids = [str(p["id"]) for p in projects_data]

    # Fetch all tasks for managed projects in one query
    try:
        tasks_resp = (
            _db()
            .table("tasks")
            .select("id, title, project_id, assigned_to, status, priority, due_date")
            .in_("project_id", project_ids)
            .execute()
        )
        all_tasks = tasks_resp.data or []
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching tasks: {str(exc)}",
        )

    # Fetch developer names for assigned users
    developer_ids = list(set(str(t["assigned_to"]) for t in all_tasks if t.get("assigned_to")))
    developers_map: dict = {}
    if developer_ids:
        try:
            devs_resp = (
                _db()
                .table("users")
                .select("id, name, email")
                .in_("id", developer_ids)
                .execute()
            )
            developers_map = {str(u["id"]): u for u in (devs_resp.data or [])}
        except Exception:
            developers_map = {}

    now = datetime.now(timezone.utc)

    def is_overdue(task: dict) -> bool:
        """Task is overdue if due_date < now AND status != COMPLETED."""
        due_date_raw = task.get("due_date")
        task_status = (task.get("status") or "").upper()
        if not due_date_raw or task_status == "COMPLETED":
            return False
        try:
            # due_date may be a date string "YYYY-MM-DD"
            if "T" in str(due_date_raw):
                due_dt = datetime.fromisoformat(str(due_date_raw).replace("Z", "+00:00"))
            else:
                due_dt = datetime.fromisoformat(f"{due_date_raw}T23:59:59+00:00")
            return due_dt < now
        except Exception:
            return False

    # Build per-project summaries
    project_summaries: List[ProjectSummary] = []
    overdue_task_list: List[OverdueTaskSummary] = []

    total_tasks_all = 0
    completed_tasks_all = 0
    in_progress_tasks_all = 0
    review_tasks_all = 0
    todo_tasks_all = 0
    overdue_tasks_all = 0

    for proj in projects_data:
        proj_id = str(proj["id"])
        proj_tasks = [t for t in all_tasks if str(t.get("project_id")) == proj_id]

        # Task counts for this project
        p_total = len(proj_tasks)
        p_completed = sum(1 for t in proj_tasks if (t.get("status") or "").upper() == "COMPLETED")
        p_in_progress = sum(1 for t in proj_tasks if (t.get("status") or "").upper() == "IN_PROGRESS")
        p_review = sum(1 for t in proj_tasks if (t.get("status") or "").upper() == "REVIEW")
        p_todo = sum(1 for t in proj_tasks if (t.get("status") or "").upper() == "TODO")
        p_overdue = sum(1 for t in proj_tasks if is_overdue(t))

        total_tasks_all += p_total
        completed_tasks_all += p_completed
        in_progress_tasks_all += p_in_progress
        review_tasks_all += p_review
        todo_tasks_all += p_todo
        overdue_tasks_all += p_overdue

        # Per-developer workload within this project
        dev_workloads: dict[str, DeveloperWorkload] = {}
        for t in proj_tasks:
            dev_id = str(t["assigned_to"]) if t.get("assigned_to") else None
            if not dev_id:
                continue
            if dev_id not in dev_workloads:
                dev_info = developers_map.get(dev_id, {})
                dev_workloads[dev_id] = DeveloperWorkload(
                    developer_id=dev_id,
                    developer_name=dev_info.get("name", "Unknown"),
                )
            wl = dev_workloads[dev_id]
            wl.total_tasks += 1
            t_status = (t.get("status") or "").upper()
            if t_status == "COMPLETED":
                wl.completed_tasks += 1
            elif t_status == "IN_PROGRESS":
                wl.in_progress_tasks += 1
            elif t_status == "REVIEW":
                wl.review_tasks += 1
            elif t_status == "TODO":
                wl.todo_tasks += 1
            if is_overdue(t):
                wl.overdue_tasks += 1

        # Collect overdue tasks for this project
        for t in proj_tasks:
            if is_overdue(t):
                dev_id = str(t["assigned_to"]) if t.get("assigned_to") else None
                dev_name = developers_map.get(dev_id, {}).get("name") if dev_id else None
                overdue_task_list.append(OverdueTaskSummary(
                    task_id=str(t["id"]),
                    task_title=t.get("title", ""),
                    project_id=proj_id,
                    project_name=proj.get("name"),
                    assigned_to=dev_id,
                    developer_name=dev_name,
                    due_date=str(t.get("due_date", "")),
                    status=(t.get("status") or "").upper(),
                    priority=(t.get("priority") or "MEDIUM").upper(),
                ))

        project_summaries.append(ProjectSummary(
            id=proj_id,
            name=proj.get("name", ""),
            description=proj.get("description"),
            status=str(proj.get("status", "planning")).lower(),
            progress=proj.get("progress") or 0,
            start_date=str(proj.get("start_date")) if proj.get("start_date") else None,
            end_date=str(proj.get("end_date")) if proj.get("end_date") else None,
            total_tasks=p_total,
            completed_tasks=p_completed,
            in_progress_tasks=p_in_progress,
            review_tasks=p_review,
            todo_tasks=p_todo,
            overdue_tasks=p_overdue,
            developers=list(dev_workloads.values()),
        ))

    # Aggregate project-level status counts
    proj_status_counts = {"active": 0, "planning": 0, "on_hold": 0, "completed": 0}
    for proj in projects_data:
        st = str(proj.get("status", "")).lower()
        if st in proj_status_counts:
            proj_status_counts[st] += 1

    # Calculate average progress across non-archived projects
    non_archived = [p for p in projects_data if str(p.get("status", "")).lower() != "archived"]
    avg_progress = 0
    if non_archived:
        avg_progress = int(sum(p.get("progress") or 0 for p in non_archived) / len(non_archived))

    return PMDashboardStats(
        total_projects=len(projects_data),
        active_projects=proj_status_counts["active"],
        planning_projects=proj_status_counts["planning"],
        on_hold_projects=proj_status_counts["on_hold"],
        completed_projects=proj_status_counts["completed"],
        total_tasks=total_tasks_all,
        completed_tasks=completed_tasks_all,
        in_progress_tasks=in_progress_tasks_all,
        review_tasks=review_tasks_all,
        todo_tasks=todo_tasks_all,
        overdue_tasks=overdue_tasks_all,
        average_progress=avg_progress,
        projects=project_summaries,
        overdue_task_list=overdue_task_list,
    )


# ---------------------------------------------------------------------------
# GET /dashboard/pm-activity  — Recent activity for PM's projects
# ---------------------------------------------------------------------------

@router.get(
    "/pm-activity",
    response_model=List[ActivityItem],
    summary="Get recent activity for managed projects",
)
async def get_pm_activity(
    limit: int = 20,
    current_user: UserOut = Depends(get_current_user),
) -> List[ActivityItem]:
    """
    Returns recent activity logs related to projects managed by the current PM.
    - PROJECT_MANAGER: activity for their managed projects + tasks within those projects.
    - ADMIN: all activity.
    Raises 403 for DEVELOPER role.
    """
    role = current_user.role.value if current_user.role else ""
    if role == RoleEnum.DEVELOPER.value:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Developers cannot access project activity feed.",
        )

    pm_id = current_user.id

    # Get managed project IDs
    try:
        if role == RoleEnum.PROJECT_MANAGER.value:
            proj_resp = (
                _db()
                .table("projects")
                .select("id")
                .eq("project_manager_id", pm_id)
                .execute()
            )
            managed_project_ids = [str(p["id"]) for p in (proj_resp.data or [])]
        else:
            # Admin sees all activity
            managed_project_ids = None
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching projects: {str(exc)}",
        )

    # Also get task IDs in those projects so we can show task-level activity
    task_ids_for_activity: list = []
    if managed_project_ids is not None and managed_project_ids:
        try:
            task_id_resp = (
                _db()
                .table("tasks")
                .select("id")
                .in_("project_id", managed_project_ids)
                .execute()
            )
            task_ids_for_activity = [str(t["id"]) for t in (task_id_resp.data or [])]
        except Exception:
            pass

    try:
        query = _db().table("activity_logs").select("*, users(id, name, email)")

        if managed_project_ids is not None:
            if not managed_project_ids:
                return []
            # Filter to activity on managed projects OR their tasks
            all_entity_ids = managed_project_ids + task_ids_for_activity
            query = query.in_("entity_id", all_entity_ids)

        resp = query.order("created_at", desc=True).limit(limit).execute()
        rows = resp.data or []

        results = []
        for r in rows:
            user_name = None
            if r.get("users"):
                user_data = r["users"]
                if isinstance(user_data, list) and user_data:
                    user_data = user_data[0]
                user_name = user_data.get("name") if isinstance(user_data, dict) else None

            results.append(ActivityItem(
                id=str(r["id"]),
                action=r.get("action", ""),
                entity_type=r.get("entity_type", ""),
                entity_id=r.get("entity_id"),
                description=r.get("description", ""),
                created_at=r.get("created_at", ""),
                user_name=user_name,
                user_id=r.get("user_id"),
            ))
        return results
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching activity: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /dashboard/pm-users — Developers available for task assignment
# ---------------------------------------------------------------------------

class DeveloperUser(BaseModel):
    id: str
    name: str
    email: str
    role: str
    is_active: bool = True


@router.get(
    "/pm-users",
    response_model=List[DeveloperUser],
    summary="Get developers available for task assignment",
)
async def get_pm_users(
    current_user: UserOut = Depends(get_current_user),
) -> List[DeveloperUser]:
    """
    Returns all active users with 'developer' role, usable for task assignment.
    Accessible by PROJECT_MANAGER and ADMIN roles.
    """
    role = current_user.role.value if current_user.role else ""
    if role == RoleEnum.DEVELOPER.value:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Developers cannot access this endpoint.",
        )
    try:
        resp = (
            _db()
            .table("users")
            .select("id, name, email, role, is_active")
            .eq("is_active", True)
            .in_("role", ["developer", "project_manager"])
            .order("name")
            .execute()
        )
        rows = resp.data or []
        return [
            DeveloperUser(
                id=str(r["id"]),
                name=r.get("name", ""),
                email=r.get("email", ""),
                role=r.get("role", "developer"),
                is_active=r.get("is_active", True),
            )
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database error fetching users: {str(exc)}",
        )
