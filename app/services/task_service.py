"""
Task Service — Business logic, validations, and database operations.
Interacts with the `public.tasks` table in Supabase.
"""
from typing import List, Optional
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.task import (
    TaskCreateRequest,
    TaskOut,
    TaskStatus,
    TaskUpdateRequest,
)
from app.schemas.user import UserOut
from app.services.activity_service import log_activity
from app.schemas.notification import NotificationType
from app.services.notification_service import NotificationService


def _db():
    return get_supabase_client()


def validate_assigned_developer(dev_id: str) -> dict:
    """
    Validate that an assigned user exists, is active, and is an eligible developer.
    """
    try:
        resp = _db().table("users").select("id, name, email, role, is_active").eq("id", dev_id).limit(1).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error looking up developer: {str(exc)}",
        )
    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Assigned developer user '{dev_id}' not found.",
        )
    dev_user = resp.data[0]
    if not dev_user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assigned developer account is inactive.",
        )
    if dev_user.get("role") not in (RoleEnum.DEVELOPER.value, RoleEnum.PROJECT_MANAGER.value, RoleEnum.ADMIN.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assigned user must be an eligible active developer.",
        )
    return dev_user


def _recalculate_project_progress(project_id: str):
    """
    Recalculates a project's progress based on its tasks:
    progress = (completed_tasks / total_tasks) * 100
    """
    if not project_id:
        return
        
    try:
        resp = _db().table("tasks").select("status").eq("project_id", project_id).execute()
        tasks = resp.data or []
        if not tasks:
            progress = 0
        else:
            total = len(tasks)
            completed = sum(1 for t in tasks if (t.get("status") or "").upper() == "COMPLETED")
            progress = int(round((completed / total) * 100))
            
        _db().table("projects").update({"progress": progress}).eq("id", project_id).execute()
    except Exception as exc:
        print(f"Error recalculating project progress for {project_id}: {exc}")


def _row_to_task_out(r: dict) -> TaskOut:
    """Convert raw database row to TaskOut schema."""
    # Ensure status is upper case for validation
    raw_status = (r.get("status") or "TODO").upper()
    try:
        task_status = TaskStatus(raw_status)
    except ValueError:
        task_status = TaskStatus.TODO

    project_data = r.get("projects") or {}
    user_data = r.get("users") or {}
    # In some cases, single-item relations return as a list in postgrest
    if isinstance(project_data, list) and len(project_data) > 0:
        project_data = project_data[0]
    elif isinstance(project_data, list):
        project_data = {}
        
    if isinstance(user_data, list) and len(user_data) > 0:
        user_data = user_data[0]
    elif isinstance(user_data, list):
        user_data = {}

    pm_id = str(project_data.get("project_manager_id")) if project_data.get("project_manager_id") else None
    pm_user = project_data.get("users") or {}
    if isinstance(pm_user, list) and len(pm_user) > 0:
        pm_user = pm_user[0]
    elif not isinstance(pm_user, dict):
        pm_user = {}
    pm_name = pm_user.get("name") if pm_user else None

    return TaskOut(
        id=str(r["id"]),
        project_id=str(r["project_id"]) if r.get("project_id") else None,
        title=r.get("title", ""),
        description=r.get("description"),
        assigned_to=str(r["assigned_to"]) if r.get("assigned_to") else None,
        status=task_status,
        priority=r.get("priority", "MEDIUM").upper(),
        due_date=r.get("due_date"),
        required_skills=r.get("required_skills", []),
        min_experience_years=r.get("min_experience_years", 0),
        estimated_effort=r.get("estimated_hours") if r.get("estimated_hours") is not None else r.get("estimated_effort"),
        estimated_hours=r.get("estimated_hours") if r.get("estimated_hours") is not None else r.get("estimated_effort"),
        created_at=r.get("created_at"),
        assigned_at=r.get("assigned_at"),
        started_at=r.get("started_at"),
        review_started_at=r.get("review_started_at"),
        completed_at=r.get("completed_at"),
        updated_at=r.get("updated_at"),
        acceptance_criteria=r.get("acceptance_criteria"),
        project_name=project_data.get("name") if project_data else None,
        developer_name=user_data.get("name") if user_data else None,
        project_manager_id=pm_id,
        project_manager_name=pm_name,
    )


def list_tasks_service(
    current_user: UserOut,
    project_id: Optional[str] = None,
) -> List[TaskOut]:
    """
    List tasks based on caller's role:
    - Developer: View their own assigned tasks only.
    - Project Manager: View tasks for their managed projects (optionally filtered by project_id).
    - Admin: View all tasks (optionally filtered by project_id).
    """
    try:
        query = _db().table("tasks").select("*, projects(name), users(name)")

        if current_user.role.value == RoleEnum.DEVELOPER.value:
            if project_id:
                # Validate the developer has access to this project
                dev_access = (
                    _db()
                    .table("tasks")
                    .select("id")
                    .eq("project_id", project_id)
                    .eq("assigned_to", current_user.id)
                    .limit(1)
                    .execute()
                )
                if not dev_access.data:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You do not have permission to view tasks for this project.",
                    )
                query = query.eq("project_id", project_id)
            else:
                # Developer: only their own tasks across all projects
                query = query.eq("assigned_to", current_user.id)

        elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
            # PM: tasks in projects they manage
            pm_projects_resp = (
                _db()
                .table("projects")
                .select("id")
                .eq("project_manager_id", current_user.id)
                .execute()
            )
            pm_project_ids = [str(p["id"]) for p in (pm_projects_resp.data or [])]

            if not pm_project_ids:
                return []

            if project_id:
                # Validate the requested project_id belongs to this PM
                if project_id not in pm_project_ids:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You do not have permission to view tasks for this project.",
                    )
                query = query.eq("project_id", project_id)
            else:
                query = query.in_("project_id", pm_project_ids)

        else:
            # Admin: all tasks, optionally filtered by project
            if project_id:
                query = query.eq("project_id", project_id)

        query = query.order("created_at", desc=True)
        resp = query.execute()
        rows = resp.data or []
        return [_row_to_task_out(r) for r in rows]

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while fetching tasks: {str(exc)}",
        )


def _recalculate_project_status(project_id: str):
    """
    Automatically set project status based on task completion rules:
    - No tasks or all TODO → PLANNING (if not ON_HOLD or ARCHIVED)
    - At least one IN_PROGRESS or REVIEW task → ACTIVE (if not ON_HOLD or ARCHIVED)
    - All tasks COMPLETED → COMPLETED (if not ON_HOLD or ARCHIVED)
    Does NOT overwrite ON_HOLD or ARCHIVED (manually controlled states).
    """
    if not project_id:
        return
    try:
        # Get current project status
        proj_resp = _db().table("projects").select("status").eq("id", project_id).limit(1).execute()
        if not proj_resp.data:
            return
        current_status = str(proj_resp.data[0].get("status", "planning")).lower()

        # Do not override manually-controlled states
        if current_status in ("on_hold", "archived"):
            return

        # Get tasks
        tasks_resp = _db().table("tasks").select("status").eq("project_id", project_id).execute()
        tasks = tasks_resp.data or []

        if not tasks:
            new_status = "planning"
        else:
            statuses = [(t.get("status") or "TODO").upper() for t in tasks]
            all_completed = all(s == "COMPLETED" for s in statuses)
            has_active = any(s in ("IN_PROGRESS", "REVIEW") for s in statuses)

            if all_completed:
                new_status = "completed"
            elif has_active:
                new_status = "active"
            else:
                new_status = "planning"

        if new_status != current_status:
            _db().table("projects").update({"status": new_status}).eq("id", project_id).execute()
    except Exception as exc:
        print(f"Error recalculating project status for {project_id}: {exc}")


def get_task_by_id_service(
    task_id: str,
    current_user: UserOut,
) -> TaskOut:
    """
    Retrieve task by ID:
    - Developer: View only if assigned_to == current_user.id
    - Project Manager: View if task's project is managed by them
    - Admin: View any task
    """
    try:
        resp = _db().table("tasks").select("*, projects(name), users(name)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        task_row = resp.data[0]

        if current_user.role.value == RoleEnum.DEVELOPER.value:
            if task_row.get("assigned_to") != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to access this task.",
                )
        elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
            # Verify task belongs to a project managed by this PM
            task_project_id = task_row.get("project_id")
            if task_project_id:
                proj_resp = _db().table("projects").select("project_manager_id").eq("id", task_project_id).limit(1).execute()
                if proj_resp.data:
                    proj_pm = proj_resp.data[0].get("project_manager_id")
                    if str(proj_pm) != str(current_user.id):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You do not have permission to access this task.",
                        )

        return _row_to_task_out(task_row)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while retrieving task: {str(exc)}",
        )




def update_task_status_service(
    task_id: str,
    payload: TaskUpdateRequest,
    current_user: UserOut,
) -> TaskOut:
    """
    Update task status:
    - Developer: Can only update their own assigned tasks.
    - Enforces valid state transitions and updates timestamps automatically.
    """
    if current_user.role.value == RoleEnum.DEVELOPER.value and not payload.status:
        # Developer can only change status for now
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be provided.",
        )

    # Fetch existing task
    try:
        resp = _db().table("tasks").select("*, projects(name), users(name)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        existing_task = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while finding task: {str(exc)}",
        )

    if current_user.role.value == RoleEnum.DEVELOPER.value:
        if existing_task.get("assigned_to") != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this task.",
            )
    elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
        # PM can only edit tasks in their managed projects
        task_project_id = existing_task.get("project_id")
        if task_project_id:
            proj_resp = _db().table("projects").select("project_manager_id").eq("id", task_project_id).limit(1).execute()
            if proj_resp.data:
                proj_pm = proj_resp.data[0].get("project_manager_id")
                if str(proj_pm) != str(current_user.id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You do not have permission to modify tasks outside your managed projects.",
                    )

    if payload.assigned_to and payload.assigned_to != existing_task.get("assigned_to"):
        validate_assigned_developer(payload.assigned_to)

    update_fields = {}
    current_time = datetime.now(timezone.utc).isoformat()
    old_status = (existing_task.get("status") or "TODO").upper()
    new_status = payload.status.value if payload.status else old_status

    if payload.status and new_status != old_status:
        # Developers cannot approve tasks to COMPLETED
        if new_status == "COMPLETED" and current_user.role == RoleEnum.DEVELOPER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Project Managers or Administrators can approve and complete tasks.",
            )

        # Validate transitions based on role
        if current_user.role == RoleEnum.DEVELOPER:
            valid_transitions = {
                "TODO": ["IN_PROGRESS"],
                "IN_PROGRESS": ["REVIEW"],
                "REVIEW": [],
                "COMPLETED": []
            }
        else:
            valid_transitions = {
                "TODO": ["IN_PROGRESS"],
                "IN_PROGRESS": ["REVIEW", "COMPLETED"],
                "REVIEW": ["COMPLETED", "IN_PROGRESS"],
                "COMPLETED": []
            }

        if new_status not in valid_transitions.get(old_status, []):
             raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status transition from {old_status} to {new_status}.",
            )

        update_fields["status"] = new_status

        # Automatic timestamps
        if new_status == "IN_PROGRESS" and old_status == "TODO":
            update_fields["started_at"] = current_time
        elif new_status == "REVIEW":
            update_fields["review_started_at"] = current_time
        elif new_status == "COMPLETED":
            update_fields["completed_at"] = current_time

    # Admins/Managers can update other fields too
    old_assigned_to = existing_task.get("assigned_to")
    if current_user.role in (RoleEnum.ADMIN, RoleEnum.PROJECT_MANAGER):
        if payload.title is not None:
            update_fields["title"] = payload.title
        if payload.description is not None:
            update_fields["description"] = payload.description
        if payload.priority is not None:
            update_fields["priority"] = payload.priority.value
        if payload.project_id is not None:
            update_fields["project_id"] = payload.project_id if payload.project_id else None
        if payload.assigned_to is not None:
            update_fields["assigned_to"] = payload.assigned_to if payload.assigned_to else None
        if payload.due_date is not None:
            update_fields["due_date"] = payload.due_date.isoformat()
        if payload.required_skills is not None:
            update_fields["required_skills"] = payload.required_skills
        if payload.min_experience_years is not None:
            update_fields["min_experience_years"] = payload.min_experience_years
        
        effort_val = payload.estimated_hours if payload.estimated_hours is not None else payload.estimated_effort
        if effort_val is not None:
            update_fields["estimated_effort"] = effort_val
            update_fields["estimated_hours"] = effort_val

    if not update_fields:
        return _row_to_task_out(existing_task)

    try:
        try:
            resp = _db().table("tasks").update(update_fields).eq("id", task_id).execute()
        except Exception as update_err:
            # If estimated_hours column doesn't exist yet in Supabase, fall back to estimated_effort only
            err_str = str(update_err).lower()
            if "estimated_hours" in err_str and "estimated_hours" in update_fields:
                fallback_fields = dict(update_fields)
                fallback_fields.pop("estimated_hours", None)
                resp = _db().table("tasks").update(fallback_fields).eq("id", task_id).execute()
            elif "estimated_effort" in err_str and "estimated_effort" in update_fields:
                fallback_fields = dict(update_fields)
                fallback_fields.pop("estimated_effort", None)
                resp = _db().table("tasks").update(fallback_fields).eq("id", task_id).execute()
            else:
                raise update_err

        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Task update failed in database.",
            )
        updated_row = resp.data[0]

        # We need the joined data for the response
        updated_row["projects"] = existing_task.get("projects")
        updated_row["users"] = existing_task.get("users")

        task_proj_id = updated_row.get("project_id") or existing_task.get("project_id")
        project_name = ""
        project_pm_id = None
        if task_proj_id:
            try:
                proj_resp = _db().table("projects").select("name, project_manager_id").eq("id", task_proj_id).limit(1).execute()
                if proj_resp.data:
                    project_name = proj_resp.data[0].get("name", "")
                    project_pm_id = str(proj_resp.data[0]["project_manager_id"]) if proj_resp.data[0].get("project_manager_id") else None
            except Exception:
                pass

        # Log Activity
        if "status" in update_fields:
            if new_status == "IN_PROGRESS" and old_status == "REVIEW":
                action = "TASK_REJECTED"
                desc = f"Task '{updated_row['title']}' was returned for revisions"
            elif new_status == "IN_PROGRESS":
                action = "TASK_STARTED"
                desc = f"Task '{updated_row['title']}' was started"
            elif new_status == "REVIEW":
                action = "TASK_SUBMITTED_FOR_REVIEW"
                desc = f"Task '{updated_row['title']}' was submitted for review"
            elif new_status == "COMPLETED":
                action = "TASK_COMPLETED"
                desc = f"Task '{updated_row['title']}' was approved and completed"
            else:
                action = "TASK_STATUS_CHANGED"
                desc = f"Task '{updated_row['title']}' moved to {updated_row['status']}"

            log_activity(
                user_id=current_user.id,
                action=action,
                entity_type="task",
                entity_id=updated_row["id"],
                project_id=task_proj_id,
                description=desc
            )

        # Log reassignment if assigned_to changed
        if "assigned_to" in update_fields and update_fields["assigned_to"] != old_assigned_to:
            log_activity(
                user_id=current_user.id,
                action="TASK_ASSIGNED",
                entity_type="task",
                entity_id=updated_row["id"],
                project_id=task_proj_id,
                description=f"Task '{updated_row['title']}' reassigned"
            )
        elif len(update_fields) > 0 and current_user.role in (RoleEnum.ADMIN, RoleEnum.PROJECT_MANAGER):
            if "status" not in update_fields and "assigned_to" not in update_fields:
                log_activity(
                    user_id=current_user.id,
                    action="TASK_UPDATED",
                    entity_type="task",
                    entity_id=updated_row["id"],
                    project_id=task_proj_id,
                    description=f"Task '{updated_row['title']}' updated"
                )

        # Recalculate project progress and auto-update project status when task status changes
        if "status" in update_fields:
            if existing_task.get("project_id"):
                _recalculate_project_progress(existing_task["project_id"])
                _recalculate_project_status(existing_task["project_id"])

        # Send notifications for status changes and reassignments
        try:
            # 1. Reassignment notification to NEW assignee only
            if "assigned_to" in update_fields and update_fields["assigned_to"] and update_fields["assigned_to"] != old_assigned_to:
                msg = (
                    f'You have been assigned the task "{updated_row.get("title")}" in project "{project_name}".'
                    if project_name
                    else f'You have been assigned to task "{updated_row.get("title")}".'
                )
                NotificationService.create_notification(
                    recipient_id=str(update_fields["assigned_to"]),
                    type=NotificationType.TASK_ASSIGNED,
                    title="New Task Assigned",
                    message=msg,
                    project_id=str(task_proj_id) if task_proj_id else None,
                    task_id=str(updated_row["id"]),
                )

            # 2. Status change notifications
            if "status" in update_fields:
                assigned_dev = updated_row.get("assigned_to") or old_assigned_to

                if new_status == "REVIEW":
                    # Notify Project Manager
                    if project_pm_id and str(project_pm_id) != str(current_user.id):
                        msg = (
                            f'Task "{updated_row.get("title")}" in project "{project_name}" has been submitted for review.'
                            if project_name
                            else f'Task "{updated_row.get("title")}" has been submitted for review.'
                        )
                        NotificationService.create_notification(
                            recipient_id=str(project_pm_id),
                            type=NotificationType.TASK_REVIEW_REQUESTED,
                            title="Task Ready for Review",
                            message=msg,
                            project_id=str(task_proj_id) if task_proj_id else None,
                            task_id=str(updated_row["id"]),
                        )
                elif new_status == "COMPLETED":
                    # Notify assigned developer
                    if assigned_dev and str(assigned_dev) != str(current_user.id):
                        msg = (
                            f'Task "{updated_row.get("title")}" in project "{project_name}" has been approved and completed.'
                            if project_name
                            else f'Task "{updated_row.get("title")}" has been approved and completed.'
                        )
                        NotificationService.create_notification(
                            recipient_id=str(assigned_dev),
                            type=NotificationType.TASK_COMPLETED,
                            title="Task Completed",
                            message=msg,
                            project_id=str(task_proj_id) if task_proj_id else None,
                            task_id=str(updated_row["id"]),
                        )
                elif old_status == "REVIEW" and new_status == "IN_PROGRESS":
                    # Notify assigned developer of revisions requested
                    if assigned_dev and str(assigned_dev) != str(current_user.id):
                        msg = (
                            f'Task "{updated_row.get("title")}" in project "{project_name}" was returned for revisions.'
                            if project_name
                            else f'Task "{updated_row.get("title")}" was returned for revisions.'
                        )
                        NotificationService.create_notification(
                            recipient_id=str(assigned_dev),
                            type=NotificationType.TASK_STATUS_CHANGED,
                            title="Task Review: Revisions Requested",
                            message=msg,
                            project_id=str(task_proj_id) if task_proj_id else None,
                            task_id=str(updated_row["id"]),
                        )
                elif old_status == "TODO" and new_status == "IN_PROGRESS":
                    # Notify Project Manager that task was started
                    if project_pm_id and str(project_pm_id) != str(current_user.id):
                        msg = (
                            f'Task "{updated_row.get("title")}" in project "{project_name}" has been started.'
                            if project_name
                            else f'Task "{updated_row.get("title")}" has been started.'
                        )
                        NotificationService.create_notification(
                            recipient_id=str(project_pm_id),
                            type=NotificationType.TASK_STATUS_CHANGED,
                            title="Task Started",
                            message=msg,
                            project_id=str(task_proj_id) if task_proj_id else None,
                            task_id=str(updated_row["id"]),
                        )
        except Exception as exc:
            print(f"Warning: Failed to dispatch task update notification: {exc}")

        # Refetch with joins to ensure accurate joined data if project/user changed
        return get_task_by_id_service(updated_row["id"], current_user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while updating task: {str(exc)}",
        )


def create_task_service(
    payload: TaskCreateRequest,
    current_user: UserOut,
) -> TaskOut:
    """
    Create a new task.
    Admins can create tasks for any project.
    Project Managers can only create tasks for their managed projects.
    """
    if current_user.role not in (RoleEnum.ADMIN, RoleEnum.PROJECT_MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admins and Managers can create tasks.",
        )

    # Verify project exists and check PM authorization
    project_name = ""
    if payload.project_id:
        proj_resp = _db().table("projects").select("id, name, project_manager_id").eq("id", payload.project_id).limit(1).execute()
        if not proj_resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        project_row = proj_resp.data[0]
        project_name = project_row.get("name", "")
        if current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
            proj_pm = project_row.get("project_manager_id")
            if str(proj_pm) != str(current_user.id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only create tasks in projects you manage.",
                )

    # Validate assigned developer
    assigned_dev_name = ""
    if payload.assigned_to:
        dev_info = validate_assigned_developer(payload.assigned_to)
        assigned_dev_name = dev_info.get("name", "")

    effort_val = payload.estimated_hours if payload.estimated_hours is not None else payload.estimated_effort
    try:
        new_task = {
            "title": payload.title,
            "description": payload.description,
            "status": payload.status.value,
            "priority": payload.priority.value,
            "project_id": payload.project_id if payload.project_id else None,
            "assigned_to": payload.assigned_to if payload.assigned_to else None,
            "due_date": payload.due_date.isoformat() if payload.due_date else None,
            "required_skills": payload.required_skills,
            "min_experience_years": payload.min_experience_years,
            "estimated_effort": effort_val,
            "estimated_hours": effort_val,
        }

        try:
            resp = _db().table("tasks").insert(new_task).execute()
        except Exception as insert_err:
            err_str = str(insert_err).lower()
            if "estimated_hours" in err_str:
                new_task.pop("estimated_hours", None)
                resp = _db().table("tasks").insert(new_task).execute()
            elif "estimated_effort" in err_str:
                new_task.pop("estimated_effort", None)
                resp = _db().table("tasks").insert(new_task).execute()
            else:
                raise insert_err

        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to insert task.",
            )

        created_row = resp.data[0]

        if created_row.get("project_id"):
            _recalculate_project_progress(created_row["project_id"])
            _recalculate_project_status(created_row["project_id"])

        # Log Activity
        log_activity(
            user_id=current_user.id,
            action="TASK_CREATED",
            entity_type="task",
            entity_id=created_row["id"],
            project_id=created_row.get("project_id"),
            description=f"Task '{created_row['title']}' created"
        )

        if created_row.get("assigned_to"):
            log_activity(
                user_id=current_user.id,
                action="TASK_ASSIGNED",
                entity_type="task",
                entity_id=created_row["id"],
                project_id=created_row.get("project_id"),
                description=f"Task '{created_row['title']}' assigned to {assigned_dev_name or 'developer'}"
            )

            # Notify assigned user if assigned at creation
            try:
                msg = (
                    f'You have been assigned the task "{created_row.get("title")}" in project "{project_name}".'
                    if project_name
                    else f'You have been assigned the task "{created_row.get("title")}".'
                )
                NotificationService.create_notification(
                    recipient_id=str(created_row["assigned_to"]),
                    type=NotificationType.TASK_ASSIGNED,
                    title="New Task Assigned",
                    message=msg,
                    project_id=str(created_row.get("project_id")) if created_row.get("project_id") else None,
                    task_id=str(created_row["id"]),
                )
            except Exception as exc:
                print(f"Warning: Failed to dispatch task assignment notification: {exc}")

        # Fetch the complete row with joins
        return get_task_by_id_service(created_row["id"], current_user)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while creating task: {str(exc)}",
        )

def delete_task_service(
    task_id: str,
    current_user: UserOut,
) -> None:
    """
    Delete a task by ID.
    Only Admins and Managers can delete tasks.
    """
    if current_user.role not in (RoleEnum.ADMIN, RoleEnum.PROJECT_MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admins and Managers can delete tasks.",
        )

    try:
        resp = _db().table("tasks").delete().eq("id", task_id).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found or could not be deleted.",
            )
            
        deleted_row = resp.data[0]
        
        # Log Activity
        log_activity(
            user_id=current_user.id,
            action="TASK_DELETED",
            entity_type="task",
            entity_id=task_id,
            project_id=deleted_row.get("project_id"),
            description=f"Task '{deleted_row.get('title', 'Unknown')}' deleted"
        )
        
        if deleted_row.get("project_id"):
            _recalculate_project_progress(deleted_row["project_id"])
            _recalculate_project_status(deleted_row["project_id"])
            
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while deleting task: {str(exc)}",
        )
