from datetime import datetime, timezone, timedelta
from typing import List, Optional

from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.user import UserOut
from app.schemas.common import WorkflowRiskOut, RiskLevel

# Thresholds
STUCK_TASK_DAYS = 3
REVIEW_DELAY_DAYS = 2
WORKLOAD_THRESHOLD = 5

def _db():
    return get_supabase_client()


def detect_and_update_risks():
    """Run detection rules for all open tasks and projects, and update the risks table."""
    now = datetime.now(timezone.utc)
    
    # We will collect all currently active risks we detect.
    # We will then compare this set with the OPEN risks in the database.
    # If a risk is in DB but no longer active, mark it resolved.
    # If a risk is active but not in DB, insert it.
    
    active_risks_detected = {} # key: (risk_type, entity_id), value: dict with risk details
    
    # 1. Fetch data
    tasks_resp = _db().table("tasks").select("*, projects(name), users(name)").execute()
    tasks = tasks_resp.data or []
    
    projects_resp = _db().table("projects").select("*").execute()
    projects = projects_resp.data or []
    
    users_resp = _db().table("users").select("id, name").execute()
    users = users_resp.data or []

    # Calculate user workload (number of active tasks: TODO, IN_PROGRESS, REVIEW)
    workloads = {u["id"]: 0 for u in users}
    
    for task in tasks:
        status = (task.get("status") or "TODO").upper()
        task_id = task["id"]
        project_id = task.get("project_id")
        project_name = task.get("projects", {}).get("name") if task.get("projects") else None
        assigned_to = task.get("assigned_to")
        title = task.get("title", "")
        
        if assigned_to and status in ("TODO", "IN_PROGRESS", "REVIEW"):
            workloads[assigned_to] = workloads.get(assigned_to, 0) + 1

        # A. STUCK_TASK
        if status == "IN_PROGRESS":
            started_at_str = task.get("started_at")
            if started_at_str:
                try:
                    started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                    if (now - started_at).days >= STUCK_TASK_DAYS:
                        key = ("STUCK_TASK", task_id)
                        active_risks_detected[key] = {
                            "risk_type": "STUCK_TASK",
                            "level": RiskLevel.MEDIUM.value,
                            "title": "Stuck Task",
                            "description": f"Task '{title}' has been in progress for over {STUCK_TASK_DAYS} days.",
                            "project_id": project_id,
                            "task_id": task_id,
                            "user_id": assigned_to,
                        }
                except ValueError:
                    pass

        # B. REVIEW_DELAY
        if status == "REVIEW":
            review_started_str = task.get("review_started_at")
            if review_started_str:
                try:
                    review_started = datetime.fromisoformat(review_started_str.replace("Z", "+00:00"))
                    if (now - review_started).days >= REVIEW_DELAY_DAYS:
                        key = ("REVIEW_DELAY", task_id)
                        active_risks_detected[key] = {
                            "risk_type": "REVIEW_DELAY",
                            "level": RiskLevel.LOW.value,
                            "title": "Review Delay",
                            "description": f"Task '{title}' is waiting for review for over {REVIEW_DELAY_DAYS} days.",
                            "project_id": project_id,
                            "task_id": task_id,
                            "user_id": assigned_to,
                        }
                except ValueError:
                    pass

        # C. OVERDUE_TASK
        if status != "COMPLETED":
            due_date_str = task.get("due_date")
            if due_date_str:
                try:
                    # due_date is usually a date string like YYYY-MM-DD
                    due_date = datetime.strptime(due_date_str.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if now > due_date:
                        key = ("OVERDUE_TASK", task_id)
                        active_risks_detected[key] = {
                            "risk_type": "OVERDUE_TASK",
                            "level": RiskLevel.HIGH.value,
                            "title": "Overdue Task",
                            "description": f"Task '{title}' is past its due date.",
                            "project_id": project_id,
                            "task_id": task_id,
                            "user_id": assigned_to,
                        }
                except ValueError:
                    pass

    # D. WORKLOAD_RISK
    for user in users:
        uid = user["id"]
        if workloads.get(uid, 0) > WORKLOAD_THRESHOLD:
            key = ("WORKLOAD_RISK", uid)
            active_risks_detected[key] = {
                "risk_type": "WORKLOAD_RISK",
                "level": RiskLevel.MEDIUM.value,
                "title": "Workload Risk",
                "description": f"Developer {user.get('name', 'Unknown')} has {workloads[uid]} active tasks.",
                "project_id": None,
                "task_id": None,
                "user_id": uid,
            }

    # E. PROJECT_DELAY
    for project in projects:
        status = (project.get("status") or "").upper()
        if status not in ("COMPLETED", "ARCHIVED"):
            end_date_str = project.get("end_date")
            progress = project.get("progress", 0)
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if now > end_date and progress < 100:
                        key = ("PROJECT_DELAY", project["id"])
                        active_risks_detected[key] = {
                            "risk_type": "PROJECT_DELAY",
                            "level": RiskLevel.HIGH.value,
                            "title": "Project Delay",
                            "description": f"Project '{project.get('name')}' is past its deadline but only {progress}% complete.",
                            "project_id": project["id"],
                            "task_id": None,
                            "user_id": project.get("project_manager_id"),
                        }
                except ValueError:
                    pass


    # 2. Reconcile with Database
    try:
        # Get all OPEN risks
        db_risks_resp = _db().table("workflow_risks").select("*").eq("is_resolved", False).execute()
        open_db_risks = db_risks_resp.data or []
        
        db_risks_map = {}
        for r in open_db_risks:
            # entity_id is task_id if present, else user_id for workload, else project_id for project_delay
            entity_id = r.get("task_id")
            if not entity_id and r.get("risk_type") == "WORKLOAD_RISK":
                entity_id = r.get("user_id")
            if not entity_id and r.get("risk_type") == "PROJECT_DELAY":
                entity_id = r.get("project_id")
            
            # The tuple ensures unique identification
            db_risks_map[(r["risk_type"], entity_id)] = r
            
        # Insert new risks
        to_insert = []
        for key, risk_data in active_risks_detected.items():
            if key not in db_risks_map:
                to_insert.append(risk_data)
                
        if to_insert:
            inserted = _db().table("workflow_risks").insert(to_insert).execute()
            # Log activity and notify for each inserted risk
            from app.services.activity_service import log_activity
            from app.services.notification_service import NotificationService
            from app.schemas.notification import NotificationType

            for r in inserted.data or []:
                desc = f"Workflow Risk detected: {r.get('title')}"
                if r.get("risk_type") == "PROJECT_DELAY":
                    log_activity(user_id=r.get("user_id"), action="RISK_DETECTED", entity_type="project", entity_id=r.get("project_id"), description=desc)
                else:
                    log_activity(user_id=r.get("user_id"), action="RISK_DETECTED", entity_type="task", entity_id=r.get("task_id"), description=desc)

                target_user = r.get("user_id")
                if target_user:
                    try:
                        NotificationService.create_notification(
                            recipient_id=str(target_user),
                            type=NotificationType.RISK_ALERT,
                            title=f"Workflow Risk: {r.get('title')}",
                            message=r.get("description", "A workflow risk was detected."),
                            project_id=str(r.get("project_id")) if r.get("project_id") else None,
                            task_id=str(r.get("task_id")) if r.get("task_id") else None,
                        )
                    except Exception as exc:
                        print(f"Warning: Failed to create risk notification: {exc}")
            
        # Resolve risks that are no longer active
        to_resolve = []
        for key, r in db_risks_map.items():
            if key not in active_risks_detected:
                to_resolve.append(r)
                
        if to_resolve:
            now_iso = now.isoformat()
            from app.services.activity_service import log_activity
            # update in chunks or loop if API doesn't support massive updates, but list is fine
            for r in to_resolve:
                _db().table("workflow_risks").update({
                    "is_resolved": True,
                    "status": "RESOLVED",
                    "resolved_at": now_iso,
                    "updated_at": now_iso
                }).eq("id", r["id"]).execute()
                
                desc = f"Workflow Risk resolved: {r.get('title')}"
                if r.get("risk_type") == "PROJECT_DELAY":
                    log_activity(user_id=r.get("user_id"), action="RISK_RESOLVED", entity_type="project", entity_id=r.get("project_id"), description=desc)
                else:
                    log_activity(user_id=r.get("user_id"), action="RISK_RESOLVED", entity_type="task", entity_id=r.get("task_id"), description=desc)
                
    except Exception as exc:
        print(f"Error during risk detection/reconciliation: {exc}")


def get_workflow_risks_service(
    current_user: UserOut,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    risk_type: Optional[str] = None
) -> List[WorkflowRiskOut]:
    """
    Get workflow risks for the current user based on RBAC:
    - ADMIN: All risks
    - PROJECT_MANAGER: Risks related to their projects, or workload risks for their developers
    - DEVELOPER: Risks related to tasks assigned to them, or their own workload
    """
    # First, run detection to ensure we have the latest
    try:
        detect_and_update_risks()
    except Exception:
        pass # If table doesn't exist yet, it will fail gracefully or we handle it in query
        
    try:
        # Fetch risks
        query = _db().table("workflow_risks").select("*, projects(name)")
        
        if current_user.role.value == RoleEnum.DEVELOPER.value:
            query = query.eq("user_id", current_user.id)
            
        elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
            # Get PM's projects
            pm_projects_resp = _db().table("projects").select("id").eq("project_manager_id", current_user.id).execute()
            pm_project_ids = [str(p["id"]) for p in (pm_projects_resp.data or [])]
            
            if not pm_project_ids:
                return []
                
            query = query.in_("project_id", pm_project_ids)

        # Apply filters
        if project_id:
            query = query.eq("project_id", project_id)
        if status:
            if status.upper() == "OPEN":
                query = query.eq("is_resolved", False)
            elif status.upper() == "RESOLVED":
                query = query.eq("is_resolved", True)
            else:
                query = query.eq("status", status.upper())
        if severity:
            query = query.eq("level", severity.title())
        if risk_type:
            query = query.eq("risk_type", risk_type)
            
        query = query.order("created_at", desc=True)
        resp = query.execute()
        rows = resp.data or []
        
        results = []
        for r in rows:
            project_data = r.get("projects") or {}
            if isinstance(project_data, list) and len(project_data) > 0:
                project_data = project_data[0]
            elif isinstance(project_data, list):
                project_data = {}
                
            results.append(
                WorkflowRiskOut(
                    id=str(r["id"]),
                    risk_type=r.get("risk_type", ""),
                    title=r.get("title", ""),
                    description=r.get("description", ""),
                    severity=RiskLevel(r.get("level", "Medium").title()),
                    project_id=str(r["project_id"]) if r.get("project_id") else None,
                    project_name=project_data.get("name") if project_data else None,
                    task_id=str(r["task_id"]) if r.get("task_id") else None,
                    status=r.get("status", "OPEN"),
                    is_resolved=r.get("is_resolved", False),
                    detected_at=r.get("detected_at", r.get("created_at", "")),
                    resolved_at=r.get("resolved_at"),
                    created_at=r.get("created_at", ""),
                    updated_at=r.get("updated_at", ""),
                )
            )
        return results
        
    except Exception as exc:
        print(f"Error fetching risks: {exc}")
        return []
