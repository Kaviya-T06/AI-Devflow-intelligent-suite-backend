"""
Project Service — Business logic, validations, and database operations.
Interacts with the `public.projects` and `public.users` tables in Supabase.
"""
from typing import Dict, List, Optional
import uuid

from fastapi import HTTPException, status

from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectManagerOut,
    ProjectOut,
    ProjectStatus,
    ProjectUpdateRequest,
)
from app.schemas.user import UserOut
from app.services.activity_service import log_activity


def _db():
    return get_supabase_client()


def _row_to_project_out(
    r: dict,
    managers_map: Optional[Dict[str, dict]] = None,
) -> ProjectOut:
    """Convert raw database row to ProjectOut schema."""
    raw_status = (r.get("status") or "planning").lower()
    try:
        proj_status = ProjectStatus(raw_status)
    except ValueError:
        proj_status = ProjectStatus.PLANNING

    pm_id = str(r["project_manager_id"]) if r.get("project_manager_id") else None
    manager_obj = None
    if pm_id and managers_map and pm_id in managers_map:
        m = managers_map[pm_id]
        manager_obj = ProjectManagerOut(
            id=str(m["id"]),
            full_name=m.get("name", ""),
            email=m.get("email", ""),
            role=m.get("role", "project_manager"),
            is_active=m.get("is_active", True),
        )

    return ProjectOut(
        id=str(r["id"]),
        name=r.get("name", ""),
        description=r.get("description"),
        status=proj_status,
        project_manager_id=pm_id,
        progress=r.get("progress", 0),
        start_date=r.get("start_date"),
        end_date=r.get("end_date"),
        created_at=r.get("created_at"),
        updated_at=r.get("updated_at"),
        member_count=r.get("member_count", 0),
        task_count=r.get("task_count", 0),
        project_manager=manager_obj,
    )


def validate_project_manager(pm_id: str) -> dict:
    """
    Validate assigned project manager:
    1. Find user in public.users.
    2. Verify user exists.
    3. Verify is_active is True.
    4. Verify role is 'project_manager'.
    """
    try:
        resp = (
            _db()
            .table("users")
            .select("id, name, email, role, is_active")
            .eq("id", pm_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error looking up project manager: {str(exc)}",
        )

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Project manager user '{pm_id}' not found.",
        )

    pm_user = resp.data[0]
    if not pm_user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assigned project manager account is inactive.",
        )

    if pm_user.get("role") != RoleEnum.PROJECT_MANAGER.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Assigned user must have the 'project_manager' role.",
        )

    return pm_user


def _populate_managers(projects_data: List[dict]) -> Dict[str, dict]:
    """Batch-fetch project managers for a list of project rows."""
    pm_ids = {
        str(p["project_manager_id"])
        for p in projects_data
        if p.get("project_manager_id")
    }
    if not pm_ids:
        return {}

    try:
        resp = (
            _db()
            .table("users")
            .select("id, name, email, role, is_active")
            .in_("id", list(pm_ids))
            .execute()
        )
        return {str(u["id"]): u for u in (resp.data or [])}
    except Exception:
        return {}


def list_projects_service(
    current_user: UserOut,
    status_filter: Optional[str] = None,
    pm_filter: Optional[str] = None,
    search: Optional[str] = None,
) -> List[ProjectOut]:
    """
    List projects based on caller's role and filter parameters:
    - Admin: View all projects (supports status, pm, and search filters).
    - Project Manager: View projects they manage.
    - Developer: View all projects (or filtered as requested).
    """
    try:
        query = _db().table("projects").select("*")

        # Role scoping
        if current_user.role == RoleEnum.PROJECT_MANAGER:
            query = query.eq("project_manager_id", current_user.id)
        elif pm_filter:
            query = query.eq("project_manager_id", pm_filter)

        if status_filter and status_filter.lower() != "all":
            query = query.eq("status", status_filter.lower())

        if search:
            # ilike search on name
            query = query.ilike("name", f"%{search}%")

        query = query.order("created_at", desc=True)
        resp = query.execute()
        rows = resp.data or []

        managers_map = _populate_managers(rows)
        return [_row_to_project_out(r, managers_map) for r in rows]

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while fetching projects: {str(exc)}",
        )


def get_project_by_id_service(
    project_id: str,
    current_user: UserOut,
) -> ProjectOut:
    """
    Retrieve project by ID:
    - Admin: View any project.
    - Project Manager: View their project (or return 403 if project belongs to another manager).
    - Developer: View project.
    """
    try:
        resp = _db().table("projects").select("*").eq("id", project_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        project_row = resp.data[0]

        # Role check for Project Manager
        if current_user.role == RoleEnum.PROJECT_MANAGER:
            if project_row.get("project_manager_id") != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to access this project.",
                )

        managers_map = _populate_managers([project_row])
        return _row_to_project_out(project_row, managers_map)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while retrieving project: {str(exc)}",
        )


def create_project_service(
    payload: ProjectCreateRequest,
    current_user: UserOut,
) -> ProjectOut:
    """
    Create a new project:
    - Admin: Can create projects and assign any valid active PM.
    - Project Manager: Can create projects (defaults to self if unassigned).
    - Developer: Forbidden (403).
    """
    if current_user.role == RoleEnum.DEVELOPER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developers are not permitted to create projects.",
        )

    # Determine assigned PM
    pm_id = payload.project_manager_id
    if current_user.role == RoleEnum.PROJECT_MANAGER:
        if not pm_id:
            pm_id = current_user.id
        elif pm_id != current_user.id:
            # If PM specifies another user, validate that user
            validate_project_manager(pm_id)
        else:
            validate_project_manager(pm_id)
    elif pm_id:
        validate_project_manager(pm_id)

    insert_payload = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "description": payload.description,
        "status": payload.status.value,
        "project_manager_id": pm_id,
        "progress": payload.progress,
        "start_date": payload.start_date.isoformat() if payload.start_date else None,
        "end_date": payload.end_date.isoformat() if payload.end_date else None,
    }

    try:
        resp = _db().table("projects").insert(insert_payload).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Project creation failed in database.",
            )
        created_row = resp.data[0]
        managers_map = _populate_managers([created_row])
        
        # Log Activity
        log_activity(
            user_id=current_user.id,
            action="PROJECT_CREATED",
            entity_type="project",
            entity_id=created_row["id"],
            description=f"Project '{created_row['name']}' was created"
        )
        
        return _row_to_project_out(created_row, managers_map)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while creating project: {str(exc)}",
        )


def update_project_service(
    project_id: str,
    payload: ProjectUpdateRequest,
    current_user: UserOut,
) -> ProjectOut:
    """
    Update project details:
    - Admin: Can edit any project, change project manager, status, progress, dates.
    - Project Manager: Can only edit projects they manage. Cannot reassign PM to another user.
    - Developer: Forbidden (403).
    """
    if current_user.role == RoleEnum.DEVELOPER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developers are not permitted to modify projects.",
        )

    # Fetch existing project
    try:
        resp = _db().table("projects").select("*").eq("id", project_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        existing_project = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while finding project: {str(exc)}",
        )

    # Role enforcement
    if current_user.role == RoleEnum.PROJECT_MANAGER:
        if existing_project.get("project_manager_id") != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify projects managed by another user.",
            )
        if payload.project_manager_id is not None and payload.project_manager_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project managers cannot reassign project ownership. Only administrators can change the project manager.",
            )

    update_data = payload.model_dump(exclude_unset=True)
    update_fields = {}

    if "name" in update_data and update_data["name"] is not None:
        update_fields["name"] = update_data["name"]

    if "description" in update_data:
        update_fields["description"] = update_data["description"]

    if "status" in update_data and update_data["status"] is not None:
        update_fields["status"] = update_data["status"].value

    if "progress" in update_data and update_data["progress"] is not None:
        update_fields["progress"] = update_data["progress"]

    if "project_manager_id" in update_data:
        pm_id = update_data["project_manager_id"]
        if pm_id != existing_project.get("project_manager_id"):
            if pm_id is not None:
                validate_project_manager(pm_id)
        update_fields["project_manager_id"] = pm_id

    # Date cross-validation with existing record
    new_start_date = (
        update_data["start_date"].isoformat() if "start_date" in update_data and update_data["start_date"] is not None
        else existing_project.get("start_date")
    )
    new_end_date = (
        update_data["end_date"].isoformat() if "end_date" in update_data and update_data["end_date"] is not None
        else existing_project.get("end_date")
    )

    if new_start_date and new_end_date and new_end_date < new_start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must not be before start_date.",
        )

    if "start_date" in update_data:
        update_fields["start_date"] = update_data["start_date"].isoformat() if update_data["start_date"] else None
    if "end_date" in update_data:
        update_fields["end_date"] = update_data["end_date"].isoformat() if update_data["end_date"] else None

    if not update_fields:
        managers_map = _populate_managers([existing_project])
        return _row_to_project_out(existing_project, managers_map)

    try:
        resp = _db().table("projects").update(update_fields).eq("id", project_id).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Project update failed in database.",
            )
        updated_row = resp.data[0]
        managers_map = _populate_managers([updated_row])
        
        # Log Activities based on what changed
        if "name" in update_fields or "description" in update_fields:
            log_activity(
                user_id=current_user.id,
                action="PROJECT_UPDATED",
                entity_type="project",
                entity_id=updated_row["id"],
                description=f"Project '{updated_row['name']}' details were updated"
            )
            
        if "status" in update_fields:
            log_activity(
                user_id=current_user.id,
                action="PROJECT_STATUS_CHANGED",
                entity_type="project",
                entity_id=updated_row["id"],
                description=f"Project '{updated_row['name']}' status changed from {existing_project.get('status')} to {updated_row['status']}"
            )
            
        if "progress" in update_fields:
            log_activity(
                user_id=current_user.id,
                action="PROJECT_PROGRESS_UPDATED",
                entity_type="project",
                entity_id=updated_row["id"],
                description=f"Project '{updated_row['name']}' progress changed from {existing_project.get('progress')}% to {updated_row['progress']}%"
            )
            
        if "project_manager_id" in update_fields:
            new_manager_name = "Unassigned"
            if updated_row["project_manager_id"] and managers_map.get(str(updated_row["project_manager_id"])):
                new_manager_name = managers_map[str(updated_row["project_manager_id"])].get("name", "Unknown User")
            log_activity(
                user_id=current_user.id,
                action="PROJECT_MANAGER_CHANGED",
                entity_type="project",
                entity_id=updated_row["id"],
                description=f"Project '{updated_row['name']}' assigned to {new_manager_name}"
            )
            
        if "start_date" in update_fields or "end_date" in update_fields:
            log_activity(
                user_id=current_user.id,
                action="PROJECT_DATES_UPDATED",
                entity_type="project",
                entity_id=updated_row["id"],
                description=f"Project '{updated_row['name']}' timeline was updated"
            )

        return _row_to_project_out(updated_row, managers_map)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while updating project: {str(exc)}",
        )


def delete_project_service(
    project_id: str,
    current_user: UserOut,
) -> ProjectOut:
    """
    Delete or safe archive a project:
    - Admin: Can archive any project.
    - Project Manager: Can archive projects they manage (cannot archive other managers' projects).
    - Developer: Forbidden (403).
    - Sets status = 'archived'.
    """
    if current_user.role == RoleEnum.DEVELOPER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developers are not permitted to delete or archive projects.",
        )

    # Fetch existing project
    try:
        resp = _db().table("projects").select("*").eq("id", project_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        existing_project = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while finding project: {str(exc)}",
        )

    if current_user.role == RoleEnum.PROJECT_MANAGER:
        if existing_project.get("project_manager_id") != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to delete or archive projects managed by another user.",
            )

    try:
        resp = (
            _db()
            .table("projects")
            .update({"status": ProjectStatus.ARCHIVED.value})
            .eq("id", project_id)
            .execute()
        )
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to archive project.",
            )
        archived_row = resp.data[0]
        managers_map = _populate_managers([archived_row])
        
        # Log Activity
        log_activity(
            user_id=current_user.id,
            action="PROJECT_ARCHIVED",
            entity_type="project",
            entity_id=archived_row["id"],
            description=f"Project '{archived_row['name']}' was archived"
        )
        
        return _row_to_project_out(archived_row, managers_map)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while archiving project: {str(exc)}",
        )
