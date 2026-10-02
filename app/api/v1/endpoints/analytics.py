from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from datetime import datetime, timezone
from collections import defaultdict
import math

from app.db.supabase_client import get_supabase_client
from app.api.deps import get_current_user
from app.schemas.user import UserOut
from app.schemas.auth import RoleEnum
from app.schemas.analytics import (
    AnalyticsDashboardData,
    ProjectMetrics,
    TaskPerformanceMetrics,
    DeveloperMetrics,
    TeamWorkflowMetrics,
    ProjectHealth,
    WorkflowTrends,
    TrendDataPoint
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])

def _db():
    return get_supabase_client()

@router.get("", response_model=AnalyticsDashboardData, summary="Get full analytics dashboard data")
async def get_analytics(
    project_id: Optional[str] = None,
    current_user: UserOut = Depends(get_current_user)
) -> AnalyticsDashboardData:
    role = current_user.role.value if current_user.role else ""
    user_id = current_user.id
    now = datetime.now(timezone.utc)

    # 1. Fetch relevant projects based on role and project_id
    try:
        proj_query = _db().table("projects").select("*")
        if project_id:
            proj_query = proj_query.eq("id", project_id)
            
        if role == RoleEnum.PROJECT_MANAGER.value:
            proj_query = proj_query.eq("project_manager_id", user_id)
            
        projects_resp = proj_query.execute()
        projects_data = projects_resp.data or []
        
        # If developer, we only get projects they are assigned to later based on tasks
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error fetching projects: {str(exc)}")

    # 2. Fetch tasks
    try:
        tasks_query = _db().table("tasks").select("*")
        
        if role == RoleEnum.ADMIN.value or role == RoleEnum.PROJECT_MANAGER.value:
            if projects_data:
                project_ids = [p["id"] for p in projects_data]
                tasks_query = tasks_query.in_("project_id", project_ids)
            else:
                # No projects, so no tasks
                tasks_query = tasks_query.in_("project_id", ["00000000-0000-0000-0000-000000000000"])
        elif role == RoleEnum.DEVELOPER.value:
            tasks_query = tasks_query.eq("assigned_to", user_id)
            if project_id:
                tasks_query = tasks_query.eq("project_id", project_id)
                
        tasks_resp = tasks_query.execute()
        tasks_data = tasks_resp.data or []
        
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error fetching tasks: {str(exc)}")

    # Filter projects for developers based on their tasks if no project_id specified
    if role == RoleEnum.DEVELOPER.value and not project_id:
        project_ids = list(set([t["project_id"] for t in tasks_data if t.get("project_id")]))
        if project_ids:
            try:
                projects_resp = _db().table("projects").select("*").in_("id", project_ids).execute()
                projects_data = projects_resp.data or []
            except Exception:
                pass
        else:
            projects_data = []

    # Fetch users for developer metrics
    developers_map = {}
    if role in (RoleEnum.ADMIN.value, RoleEnum.PROJECT_MANAGER.value):
        dev_ids = list(set([t["assigned_to"] for t in tasks_data if t.get("assigned_to")]))
        if dev_ids:
            try:
                devs_resp = _db().table("users").select("id, name, email").in_("id", dev_ids).execute()
                developers_map = {str(u["id"]): u for u in (devs_resp.data or [])}
            except Exception:
                pass

    # Helper function for parsing dates safely
    def parse_dt(dt_str):
        if not dt_str:
            return None
        try:
            if "T" in str(dt_str):
                return datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
            return datetime.fromisoformat(f"{dt_str}T23:59:59+00:00")
        except:
            return None

    def is_overdue(task: dict) -> bool:
        due_date_raw = task.get("due_date")
        task_status = (task.get("status") or "").upper()
        if not due_date_raw or task_status == "COMPLETED":
            return False
        due_dt = parse_dt(due_date_raw)
        if due_dt and due_dt < now:
            return True
        return False

    # Initialize aggregators
    proj_metrics = ProjectMetrics()
    task_perf = TaskPerformanceMetrics()
    dev_metrics_dict = {}
    health = ProjectHealth()
    
    # Trend aggregators
    completed_trend = defaultdict(int)
    created_trend = defaultdict(int)
    overdue_trend = defaultdict(int)
    completion_times_trend = defaultdict(list)
    
    # Task time metrics
    task_completion_times = []
    task_cycle_times = []
    task_in_progress_times = []
    task_review_times = []
    
    for task in tasks_data:
        status = (task.get("status") or "").upper()
        
        proj_metrics.total_tasks += 1
        task_perf.total_tasks_created += 1
        
        created_at = parse_dt(task.get("created_at"))
        started_at = parse_dt(task.get("started_at"))
        review_started_at = parse_dt(task.get("review_started_at"))
        completed_at = parse_dt(task.get("completed_at"))
        
        if created_at:
            created_trend[created_at.strftime("%Y-%m-%d")] += 1
            
        if status == "COMPLETED":
            proj_metrics.completed_tasks += 1
            task_perf.total_completed += 1
            if completed_at:
                completed_trend[completed_at.strftime("%Y-%m-%d")] += 1
                if started_at:
                    hours = (completed_at - started_at).total_seconds() / 3600
                    if hours >= 0:
                        task_completion_times.append(hours)
                        completion_times_trend[completed_at.strftime("%Y-%m-%d")].append(hours)
                if created_at:
                    hours = (completed_at - created_at).total_seconds() / 3600
                    if hours >= 0:
                        task_cycle_times.append(hours)
                if review_started_at:
                    hours = (completed_at - review_started_at).total_seconds() / 3600
                    if hours >= 0:
                        task_review_times.append(hours)
                # In progress time logic
                ip_end_time = review_started_at if review_started_at else completed_at
                if started_at and ip_end_time:
                    hours = (ip_end_time - started_at).total_seconds() / 3600
                    if hours >= 0:
                        task_in_progress_times.append(hours)
                        
        elif status == "TODO":
            proj_metrics.pending_tasks += 1
        elif status == "IN_PROGRESS":
            proj_metrics.in_progress_tasks += 1
        elif status == "REVIEW":
            proj_metrics.review_tasks += 1

        is_task_overdue = is_overdue(task)
        if is_task_overdue:
            proj_metrics.overdue_tasks += 1
            task_perf.total_overdue += 1
            due_dt = parse_dt(task.get("due_date"))
            if due_dt:
                overdue_trend[due_dt.strftime("%Y-%m-%d")] += 1

        # Developer metrics logic
        if role in (RoleEnum.ADMIN.value, RoleEnum.PROJECT_MANAGER.value):
            dev_id = str(task.get("assigned_to")) if task.get("assigned_to") else None
            if dev_id:
                if dev_id not in dev_metrics_dict:
                    dev_info = developers_map.get(dev_id, {})
                    dev_metrics_dict[dev_id] = DeveloperMetrics(
                        developer_id=dev_id,
                        developer_name=dev_info.get("name", "Unknown")
                    )
                dev_m = dev_metrics_dict[dev_id]
                dev_m.tasks_assigned += 1
                if status == "COMPLETED":
                    dev_m.tasks_completed += 1
                elif status == "IN_PROGRESS":
                    dev_m.tasks_in_progress += 1
                elif status == "REVIEW":
                    dev_m.tasks_waiting_for_review += 1
                
                if is_task_overdue:
                    dev_m.overdue_tasks += 1

    if proj_metrics.total_tasks > 0:
        proj_metrics.completion_percentage = (proj_metrics.completed_tasks / proj_metrics.total_tasks) * 100
        task_perf.completion_rate = (task_perf.total_completed / task_perf.total_tasks_created) * 100
    
    if task_completion_times:
        proj_metrics.average_task_completion_time_hours = sum(task_completion_times) / len(task_completion_times)
        task_perf.average_completion_time_hours = sum(task_completion_times) / len(task_completion_times)
    if task_cycle_times:
        proj_metrics.average_task_cycle_time_hours = sum(task_cycle_times) / len(task_cycle_times)
    if task_in_progress_times:
        task_perf.average_time_in_progress_hours = sum(task_in_progress_times) / len(task_in_progress_times)
    if task_review_times:
        task_perf.average_review_duration_hours = sum(task_review_times) / len(task_review_times)

    # Finalize developer metrics
    team_workflow_metrics = None
    if role in (RoleEnum.ADMIN.value, RoleEnum.PROJECT_MANAGER.value):
        for dev_m in dev_metrics_dict.values():
            if dev_m.tasks_assigned > 0:
                dev_m.completion_rate = (dev_m.tasks_completed / dev_m.tasks_assigned) * 100
        team_workflow_metrics = TeamWorkflowMetrics(developers=list(dev_metrics_dict.values()))
    
    # Project Health
    total_prog = 0
    valid_projs = 0
    for p in projects_data:
        if str(p.get("status", "")).lower() != "archived":
            total_prog += (p.get("progress") or 0)
            valid_projs += 1
            
    if valid_projs > 0:
        health.project_progress = total_prog / valid_projs
        
    health.completion_rate = task_perf.completion_rate
    health.overdue_task_count = task_perf.total_overdue
    health.tasks_waiting_for_review = proj_metrics.review_tasks
    health.active_tasks = proj_metrics.in_progress_tasks
    health.remaining_tasks = proj_metrics.pending_tasks + proj_metrics.in_progress_tasks + proj_metrics.review_tasks

    # Workflow Trends
    trends = WorkflowTrends()
    
    def dict_to_trend(d):
        return [TrendDataPoint(date=k, value=v) for k, v in sorted(d.items())]
        
    trends.tasks_completed_over_time = dict_to_trend(completed_trend)
    trends.tasks_created_over_time = dict_to_trend(created_trend)
    trends.overdue_tasks_over_time = dict_to_trend(overdue_trend)
    
    avg_completion_times_trend = {
        date: sum(times) / len(times) for date, times in completion_times_trend.items()
    }
    trends.average_completion_time_over_time = dict_to_trend(avg_completion_times_trend)
    
    # Creating a simple cumulative completion progress trend (heuristic for demonstration as we don't have historical progress data easily available without activity logs)
    # The requirement says: "Where sufficient historical data exists, calculate trends... project completion progress over time"
    # To keep it accurate, I'll use the ratio of cumulative completed tasks to cumulative created tasks per day.
    dates = sorted(list(set(list(created_trend.keys()) + list(completed_trend.keys()))))
    cum_created = 0
    cum_completed = 0
    for d in dates:
        cum_created += created_trend.get(d, 0)
        cum_completed += completed_trend.get(d, 0)
        prog = (cum_completed / cum_created * 100) if cum_created > 0 else 0
        trends.project_progress_over_time.append(TrendDataPoint(date=d, value=prog))

    return AnalyticsDashboardData(
        project_metrics=proj_metrics,
        task_performance_metrics=task_perf,
        team_workflow_metrics=team_workflow_metrics,
        project_health=health,
        workflow_trends=trends
    )
