"""
Task Submission & Review Service — business logic, authorizations, database operations,
dual-layer persistence, GitHub evidence integration, notifications, and project progress recalculation.
"""
import os
from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Any
import uuid

from fastapi import HTTPException, status
import httpx

from app.core.config import settings
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.notification import NotificationType
from app.schemas.task import TaskOut, TaskStatus
from app.schemas.task_submission import (
    GitHubEvidenceOut,
    PendingReviewTaskOut,
    ReviewDecisionEnum,
    SubmissionStatusEnum,
    TaskAttachmentOut,
    TaskReviewActionRequest,
    TaskReviewOut,
    TaskSubmissionCreateRequest,
    TaskSubmissionOut,
)
from app.schemas.user import UserOut
from app.services.activity_service import log_activity
from app.services.notification_service import NotificationService
from app.services.task_service import (
    _recalculate_project_progress,
    _recalculate_project_status,
    _row_to_task_out,
)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file
MAX_TOTAL_SIZE = 25 * 1024 * 1024  # 25 MB total across files
ALLOWED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp",
    ".pdf",
    ".txt", ".csv",
    ".doc", ".docx",
    ".xls", ".xlsx",
    ".zip",
}
ALLOWED_MIME_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp",
    "application/pdf",
    "text/plain", "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip", "application/x-zip-compressed",
    "application/octet-stream",
}
ATTACHMENT_BUCKET = "task-review-attachments"


def _db():
    return get_supabase_client()


def _ensure_attachment_bucket():
    try:
        _db().storage.create_bucket(ATTACHMENT_BUCKET, options={"public": False})
    except Exception:
        pass


def _save_attachment_record(attachment_data: dict) -> dict:
    """
    Attempts to insert into public.task_submission_attachments.
    Falls back gracefully to activity_logs if table is not yet in PostgREST schema cache.
    """
    att_id = attachment_data.get("id") or str(uuid.uuid4())
    attachment_data["id"] = att_id
    created_at = attachment_data.get("created_at") or datetime.now(timezone.utc).isoformat()
    attachment_data["created_at"] = created_at

    try:
        resp = _db().table("task_submission_attachments").insert(attachment_data).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        err_str = str(exc).lower()
        if "schema cache" in err_str or "pgrst205" in err_str or "does not exist" in err_str:
            pass
        else:
            print(f"Notice: task_submission_attachments insert fallback due to: {exc}")

    # Fallback to activity_logs
    _db().table("activity_logs").insert({
        "user_id": attachment_data["uploaded_by"],
        "action": "TASK_SUBMISSION_ATTACHMENT",
        "entity_type": "task",
        "entity_id": attachment_data["task_id"],
        "description": f"Uploaded attachment {attachment_data.get('filename')}",
        "metadata": attachment_data,
        "created_at": created_at,
    }).execute()
    return attachment_data


def _fetch_task_attachments_raw(task_id: str) -> List[dict]:
    """Retrieve raw attachment rows from task_submission_attachments table or activity_logs."""
    attachments = []
    try:
        resp = _db().table("task_submission_attachments").select("*").eq("task_id", task_id).order("created_at", desc=False).execute()
        if resp.data:
            attachments = resp.data
    except Exception:
        pass

    if not attachments:
        try:
            logs_resp = (
                _db()
                .table("activity_logs")
                .select("id, user_id, description, metadata, created_at")
                .eq("entity_id", task_id)
                .eq("action", "TASK_SUBMISSION_ATTACHMENT")
                .order("created_at", desc=False)
                .execute()
            )
            for log in (logs_resp.data or []):
                meta = log.get("metadata") or {}
                att_row = {
                    "id": meta.get("id") or str(log["id"]),
                    "task_id": task_id,
                    "submission_id": meta.get("submission_id", ""),
                    "uploaded_by": meta.get("uploaded_by") or log.get("user_id"),
                    "filename": meta.get("filename", "attachment"),
                    "storage_path": meta.get("storage_path", ""),
                    "mime_type": meta.get("mime_type", "application/octet-stream"),
                    "size": int(meta.get("size", 0)),
                    "created_at": meta.get("created_at") or log["created_at"],
                }
                attachments.append(att_row)
        except Exception as exc:
            print(f"Notice: Failed to fetch attachments from activity_logs: {exc}")

    return attachments


# ---------------------------------------------------------------------------
# Helpers for Dual-layer Persistence (task_submissions / task_reviews <-> activity_logs)
# ---------------------------------------------------------------------------

def _save_submission_record(submission_data: dict) -> dict:
    """
    Attempts to insert into public.task_submissions.
    Falls back gracefully to activity_logs if table is not yet in PostgREST schema cache.
    """
    sub_id = submission_data.get("id") or str(uuid.uuid4())
    submission_data["id"] = sub_id
    created_at = submission_data.get("created_at") or datetime.now(timezone.utc).isoformat()
    submission_data["created_at"] = created_at
    submission_data["updated_at"] = created_at

    try:
        resp = _db().table("task_submissions").insert(submission_data).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        # Check if table missing error (PGRST205)
        err_str = str(exc).lower()
        if "schema cache" in err_str or "pgrst205" in err_str or "does not exist" in err_str:
            pass
        else:
            print(f"Notice: task_submissions insert fallback due to: {exc}")

    # Fallback to activity_logs
    _db().table("activity_logs").insert({
        "user_id": submission_data["submitted_by"],
        "action": "TASK_SUBMISSION",
        "entity_type": "task",
        "entity_id": submission_data["task_id"],
        "description": f"Submitted task for review (Submission #{submission_data.get('submission_number', 1)})",
        "metadata": submission_data,
        "created_at": created_at,
    }).execute()
    return submission_data


def _save_review_record(review_data: dict) -> dict:
    """
    Attempts to insert into public.task_reviews.
    Falls back gracefully to activity_logs if table is not yet in PostgREST schema cache.
    """
    rev_id = review_data.get("id") or str(uuid.uuid4())
    review_data["id"] = rev_id
    created_at = review_data.get("created_at") or datetime.now(timezone.utc).isoformat()
    review_data["created_at"] = created_at

    try:
        resp = _db().table("task_reviews").insert(review_data).execute()
        if resp.data:
            return resp.data[0]
    except Exception as exc:
        err_str = str(exc).lower()
        if "schema cache" in err_str or "pgrst205" in err_str or "does not exist" in err_str:
            pass
        else:
            print(f"Notice: task_reviews insert fallback due to: {exc}")

    # Fallback to activity_logs
    _db().table("activity_logs").insert({
        "user_id": review_data["reviewer_id"],
        "action": "TASK_REVIEW",
        "entity_type": "task",
        "entity_id": review_data["task_id"],
        "description": f"Task review decision: {review_data.get('decision')}",
        "metadata": review_data,
        "created_at": created_at,
    }).execute()
    return review_data


def _update_submission_status(task_id: str, submission_id: str, new_status: str):
    """Update status of a specific submission in table or activity_logs."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        _db().table("task_submissions").update({
            "status": new_status,
            "updated_at": now_iso,
        }).eq("id", submission_id).execute()
    except Exception:
        # Fallback: record status change event in activity_logs
        try:
            _db().table("activity_logs").insert({
                "action": "TASK_SUBMISSION_STATUS_UPDATED",
                "entity_type": "task",
                "entity_id": task_id,
                "description": f"Submission {submission_id} status updated to {new_status}",
                "metadata": {"submission_id": submission_id, "status": new_status, "updated_at": now_iso},
            }).execute()
        except Exception:
            pass


def _fetch_task_submissions_raw(task_id: str) -> List[dict]:
    """Retrieve raw submission rows from task_submissions table or activity_logs."""
    submissions = []
    # 1. Try dedicated table
    try:
        resp = _db().table("task_submissions").select("*").eq("task_id", task_id).order("submission_number", desc=False).execute()
        if resp.data:
            submissions = resp.data
    except Exception:
        pass

    # 2. If dedicated table yielded no records, check activity_logs
    if not submissions:
        try:
            logs_resp = (
                _db()
                .table("activity_logs")
                .select("id, user_id, description, metadata, created_at")
                .eq("entity_id", task_id)
                .eq("action", "TASK_SUBMISSION")
                .order("created_at", desc=False)
                .execute()
            )
            for log in (logs_resp.data or []):
                meta = log.get("metadata") or {}
                sub_row = {
                    "id": meta.get("id") or str(log["id"]),
                    "task_id": task_id,
                    "submitted_by": meta.get("submitted_by") or log.get("user_id"),
                    "work_summary": meta.get("work_summary", ""),
                    "implementation_details": meta.get("implementation_details", ""),
                    "testing_performed": meta.get("testing_performed", ""),
                    "pull_request_url": meta.get("pull_request_url"),
                    "deliverable_url": meta.get("deliverable_url"),
                    "additional_comments": meta.get("additional_comments"),
                    "submission_number": meta.get("submission_number", 1),
                    "status": meta.get("status", "PENDING"),
                    "created_at": meta.get("created_at") or log["created_at"],
                    "updated_at": meta.get("updated_at") or log["created_at"],
                }
                submissions.append(sub_row)
        except Exception as exc:
            print(f"Notice: Failed to fetch task submissions from activity_logs: {exc}")

    # Check for submission status updates in activity logs (if fallback was used)
    try:
        status_updates = (
            _db()
            .table("activity_logs")
            .select("metadata")
            .eq("entity_id", task_id)
            .eq("action", "TASK_SUBMISSION_STATUS_UPDATED")
            .order("created_at", desc=False)
            .execute()
        )
        for update in (status_updates.data or []):
            meta = update.get("metadata") or {}
            sub_id = meta.get("submission_id")
            new_st = meta.get("status")
            if sub_id and new_st:
                for s in submissions:
                    if str(s["id"]) == str(sub_id):
                        s["status"] = new_st
    except Exception:
        pass

    return submissions


def _fetch_task_reviews_raw(task_id: str) -> List[dict]:
    """Retrieve raw review rows from task_reviews table or activity_logs."""
    reviews = []
    # 1. Try dedicated table
    try:
        resp = _db().table("task_reviews").select("*").eq("task_id", task_id).order("created_at", desc=False).execute()
        if resp.data:
            reviews = resp.data
    except Exception:
        pass

    # 2. Check activity_logs
    if not reviews:
        try:
            logs_resp = (
                _db()
                .table("activity_logs")
                .select("id, user_id, description, metadata, created_at")
                .eq("entity_id", task_id)
                .eq("action", "TASK_REVIEW")
                .order("created_at", desc=False)
                .execute()
            )
            for log in (logs_resp.data or []):
                meta = log.get("metadata") or {}
                rev_row = {
                    "id": meta.get("id") or str(log["id"]),
                    "task_id": task_id,
                    "submission_id": meta.get("submission_id", ""),
                    "reviewer_id": meta.get("reviewer_id") or log.get("user_id"),
                    "decision": meta.get("decision", "APPROVE"),
                    "review_comment": meta.get("review_comment"),
                    "created_at": meta.get("created_at") or log["created_at"],
                }
                reviews.append(rev_row)
        except Exception as exc:
            print(f"Notice: Failed to fetch task reviews from activity_logs: {exc}")

    return reviews


def _batch_fetch_users(user_ids: List[str]) -> Dict[str, dict]:
    """Batch fetch user dicts (id, name, email) for display."""
    clean_ids = list(set(str(u) for u in user_ids if u))
    if not clean_ids:
        return {}
    try:
        resp = _db().table("users").select("id, name, email").in_("id", clean_ids).execute()
        return {str(u["id"]): u for u in (resp.data or [])}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 1. Developer Task Submission Service
# ---------------------------------------------------------------------------

async def submit_task_for_review_service(
    task_id: str,
    payload: TaskSubmissionCreateRequest,
    current_user: UserOut,
    files: Optional[List[Any]] = None,
) -> TaskSubmissionOut:
    """
    Submit an assigned task for Project Manager review:
    1. Authenticate user and validate assignment (only assigned developer or admin).
    2. Confirm task belongs to a project the developer is authorized to access.
    3. Ensure valid transition: TODO -> IN_PROGRESS -> REVIEW.
    4. Validate and upload any attached files to private Supabase Storage bucket `task-review-attachments`.
    5. Save submission with work summary, implementation details, testing, and optional URLs.
    6. Update task status to REVIEW and record review_started_at.
    7. Preserve previous submissions and review history.
    8. Recalculate project progress (REVIEW tasks do NOT count as completed).
    9. Dispatch notification to project manager.
    10. Log activity in activity_logs.
    """
    import inspect

    # 1. Fetch task
    try:
        resp = _db().table("tasks").select("*, projects(id, name, project_manager_id)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        task_row = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error looking up task: {str(exc)}",
        )

    # 2. Authorization: Only assigned developer or admin can submit
    assigned_to = str(task_row.get("assigned_to")) if task_row.get("assigned_to") else None
    if current_user.role.value == RoleEnum.DEVELOPER.value:
        if assigned_to != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot submit tasks assigned to other developers.",
            )

    # 3. Status transition verification
    current_status = (task_row.get("status") or "TODO").upper()
    if current_status == "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is already completed and cannot be resubmitted for review.",
        )
    if current_status == "REVIEW":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is currently under review. Awaiting project manager decision.",
        )

    project_info = task_row.get("projects") or {}
    if isinstance(project_info, list) and len(project_info) > 0:
        project_info = project_info[0]
    elif isinstance(project_info, list):
        project_info = {}

    project_id = str(task_row.get("project_id")) if task_row.get("project_id") else None
    pm_id = str(project_info.get("project_manager_id")) if project_info.get("project_manager_id") else None
    project_name = project_info.get("name") or "Project"

    # 4. Count existing submissions for auto-incrementing submission_number
    existing_submissions = _fetch_task_submissions_raw(task_id)
    submission_number = len(existing_submissions) + 1

    submission_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    # 5. Process and validate file attachments (10 MB per file, 25 MB total limit)
    uploaded_attachments: List[dict] = []
    if files:
        _ensure_attachment_bucket()
        total_size = 0
        prepared_files = []

        for file in files:
            filename = getattr(file, "filename", None) or "attachment"
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported file type '{ext}' for file '{filename}'. Allowed types: PNG, JPG, JPEG, WEBP, PDF, and common documents (TXT, CSV, DOC, DOCX, XLS, XLSX, ZIP).",
                )

            # Read file bytes
            if hasattr(file, "read"):
                if inspect.iscoroutinefunction(file.read):
                    content = await file.read()
                else:
                    content = file.read()
            elif isinstance(file, (bytes, bytearray)):
                content = bytes(file)
            else:
                content = b""

            file_size = len(content)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File '{filename}' ({file_size / (1024*1024):.2f} MB) exceeds the maximum allowed limit of 10 MB per file.",
                )

            total_size += file_size
            if total_size > MAX_TOTAL_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Total attachment size ({total_size / (1024*1024):.2f} MB) exceeds the maximum allowed limit of 25 MB per submission.",
                )

            mime_type = getattr(file, "content_type", None) or "application/octet-stream"
            if mime_type == "application/octet-stream" or not mime_type:
                if ext in (".png", ".webp"):
                    mime_type = f"image/{ext[1:]}"
                elif ext in (".jpg", ".jpeg"):
                    mime_type = "image/jpeg"
                elif ext == ".pdf":
                    mime_type = "application/pdf"
                elif ext == ".txt":
                    mime_type = "text/plain"
                elif ext == ".csv":
                    mime_type = "text/csv"

            prepared_files.append({
                "filename": filename,
                "content": content,
                "size": file_size,
                "mime_type": mime_type,
            })

        # Upload files to private Supabase Storage bucket
        for item in prepared_files:
            att_id = str(uuid.uuid4())
            safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", item["filename"])
            storage_path = f"tasks/{task_id}/submissions/{submission_id}/{att_id}_{safe_name}"

            try:
                _db().storage.from_(ATTACHMENT_BUCKET).upload(
                    storage_path,
                    item["content"],
                    file_options={"content-type": item["mime_type"], "upsert": "true"},
                )
            except Exception as storage_err:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to store file in private storage bucket: {str(storage_err)}",
                )

            att_record = {
                "id": att_id,
                "task_id": task_id,
                "submission_id": submission_id,
                "uploaded_by": str(current_user.id),
                "filename": item["filename"],
                "storage_path": storage_path,
                "mime_type": item["mime_type"],
                "size": item["size"],
                "created_at": now_iso,
            }
            saved_att = _save_attachment_record(att_record)
            uploaded_attachments.append(saved_att)

    # 6. Submission record
    submission_record = {
        "id": submission_id,
        "task_id": task_id,
        "submitted_by": str(current_user.id),
        "work_summary": payload.work_summary.strip(),
        "implementation_details": payload.implementation_details.strip(),
        "testing_performed": payload.testing_performed.strip(),
        "pull_request_url": payload.pull_request_url.strip() if payload.pull_request_url else None,
        "deliverable_url": payload.deliverable_url.strip() if payload.deliverable_url else None,
        "additional_comments": payload.additional_comments.strip() if payload.additional_comments else None,
        "submission_number": submission_number,
        "status": SubmissionStatusEnum.PENDING.value,
        "created_at": now_iso,
    }

    # 7. Persist submission
    saved_sub = _save_submission_record(submission_record)

    # 8. Update task status to REVIEW
    update_task_fields = {
        "status": TaskStatus.REVIEW.value,
        "review_started_at": now_iso,
        "updated_at": now_iso,
    }
    # If task was in TODO, also mark started_at
    if current_status == "TODO" and not task_row.get("started_at"):
        update_task_fields["started_at"] = now_iso

    try:
        _db().table("tasks").update(update_task_fields).eq("id", task_id).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update task status to REVIEW: {str(exc)}",
        )

    # 9. Recalculate project progress & status (tasks in REVIEW do NOT count as completed)
    if project_id:
        _recalculate_project_progress(project_id)
        _recalculate_project_status(project_id)

    # 10. Activity log
    log_activity(
        user_id=current_user.id,
        action="TASK_SUBMITTED_FOR_REVIEW",
        entity_type="task",
        entity_id=task_id,
        project_id=project_id,
        description=f"Task '{task_row.get('title')}' was submitted for review (Submission #{submission_number}, {len(uploaded_attachments)} attachments)",
        metadata={
            "submission_id": submission_id,
            "submission_number": submission_number,
            "pull_request_url": payload.pull_request_url,
            "deliverable_url": payload.deliverable_url,
            "attachments_count": len(uploaded_attachments),
            "attachments": [
                {
                    "id": a["id"],
                    "filename": a["filename"],
                    "size": a["size"],
                    "mime_type": a["mime_type"],
                }
                for a in uploaded_attachments
            ],
        }
    )

    # 11. Notify Project Manager
    if pm_id and str(pm_id) != str(current_user.id):
        try:
            NotificationService.create_notification(
                recipient_id=pm_id,
                type=NotificationType.TASK_REVIEW_REQUESTED,
                title="Task Submitted for Review",
                message=f'Task "{task_row.get("title")}" in project "{project_name}" was submitted for review by {current_user.name} ({len(uploaded_attachments)} attachments).',
                project_id=project_id,
                task_id=task_id,
            )
        except Exception as notif_err:
            print(f"Notice: Failed to dispatch review notification to PM: {notif_err}")

    # Build response attachment models
    attachment_models = [
        TaskAttachmentOut(
            id=str(a["id"]),
            task_id=task_id,
            submission_id=submission_id,
            uploaded_by=str(current_user.id),
            uploader_name=current_user.name,
            filename=a["filename"],
            storage_path=a["storage_path"],
            mime_type=a["mime_type"],
            size=int(a["size"]),
            created_at=datetime.fromisoformat(str(a["created_at"]).replace("Z", "+00:00")),
            preview_url=f"/api/v1/tasks/{task_id}/attachments/{a['id']}/preview",
            download_url=f"/api/v1/tasks/{task_id}/attachments/{a['id']}/download",
        )
        for a in uploaded_attachments
    ]

    return TaskSubmissionOut(
        id=saved_sub["id"],
        task_id=task_id,
        submitted_by=str(current_user.id),
        submitter_name=current_user.name,
        submitter_email=current_user.email,
        work_summary=saved_sub["work_summary"],
        implementation_details=saved_sub["implementation_details"],
        testing_performed=saved_sub["testing_performed"],
        pull_request_url=saved_sub.get("pull_request_url"),
        deliverable_url=saved_sub.get("deliverable_url"),
        additional_comments=saved_sub.get("additional_comments"),
        submission_number=saved_sub.get("submission_number", 1),
        status=SubmissionStatusEnum(saved_sub.get("status", "PENDING")),
        created_at=datetime.fromisoformat(saved_sub["created_at"].replace("Z", "+00:00")),
        reviews=[],
        attachments=attachment_models,
    )


# ---------------------------------------------------------------------------
# 2. Project Manager Pending Reviews Listing
# ---------------------------------------------------------------------------

def list_pending_reviews_service(
    current_user: UserOut,
    project_id: Optional[str] = None,
) -> List[PendingReviewTaskOut]:
    """
    List tasks currently in REVIEW status for projects managed by the current user.
    - Project Manager: only tasks in their managed projects.
    - Admin: all review tasks (optionally filtered by project_id).
    - Developer: Forbidden.
    """
    if current_user.role.value == RoleEnum.DEVELOPER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developers are not authorized to access pending reviews.",
        )

    try:
        # Determine accessible project IDs
        accessible_project_ids = []
        if current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
            pm_proj_resp = _db().table("projects").select("id").eq("project_manager_id", current_user.id).execute()
            accessible_project_ids = [str(p["id"]) for p in (pm_proj_resp.data or [])]
            if not accessible_project_ids:
                return []
            if project_id and project_id not in accessible_project_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to view reviews for this project.",
                )

        # Query tasks in REVIEW status
        query = (
            _db()
            .table("tasks")
            .select("*, projects(id, name, project_manager_id, users:project_manager_id(name)), users:assigned_to(id, name, email)")
            .eq("status", "REVIEW")
        )

        if current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
            if project_id:
                query = query.eq("project_id", project_id)
            else:
                query = query.in_("project_id", accessible_project_ids)
        elif project_id:
            query = query.eq("project_id", project_id)

        query = query.order("review_started_at", desc=True)
        resp = query.execute()
        rows = resp.data or []

        results = []
        for r in rows:
            task_id = str(r["id"])
            proj_data = r.get("projects") or {}
            if isinstance(proj_data, list) and len(proj_data) > 0:
                proj_data = proj_data[0]
            elif isinstance(proj_data, list):
                proj_data = {}

            dev_data = r.get("users") or {}
            if isinstance(dev_data, list) and len(dev_data) > 0:
                dev_data = dev_data[0]
            elif isinstance(dev_data, list):
                dev_data = {}

            # Fetch submissions to obtain submission count & latest submission
            submissions_raw = _fetch_task_submissions_raw(task_id)
            latest_sub = None
            if submissions_raw:
                # Latest is last in list (highest submission number)
                last_raw = submissions_raw[-1]
                submitter_id = str(last_raw.get("submitted_by")) if last_raw.get("submitted_by") else ""
                latest_sub_id = str(last_raw["id"])

                # Fetch attachments for latest submission
                all_atts_raw = _fetch_task_attachments_raw(task_id)
                latest_atts = [
                    TaskAttachmentOut(
                        id=str(a["id"]),
                        task_id=task_id,
                        submission_id=latest_sub_id,
                        uploaded_by=str(a.get("uploaded_by", "")),
                        uploader_name=dev_data.get("name") if str(dev_data.get("id")) == str(a.get("uploaded_by")) else None,
                        filename=a.get("filename", "attachment"),
                        storage_path=a.get("storage_path", ""),
                        mime_type=a.get("mime_type", "application/octet-stream"),
                        size=int(a.get("size", 0)),
                        created_at=datetime.fromisoformat(str(a["created_at"]).replace("Z", "+00:00")),
                        preview_url=f"/api/v1/tasks/{task_id}/attachments/{a['id']}/preview",
                        download_url=f"/api/v1/tasks/{task_id}/attachments/{a['id']}/download",
                    )
                    for a in all_atts_raw
                    if str(a.get("submission_id")) == latest_sub_id
                ]

                latest_sub = TaskSubmissionOut(
                    id=latest_sub_id,
                    task_id=task_id,
                    submitted_by=submitter_id,
                    submitter_name=dev_data.get("name") if str(dev_data.get("id")) == submitter_id else None,
                    submitter_email=dev_data.get("email") if str(dev_data.get("id")) == submitter_id else None,
                    work_summary=last_raw.get("work_summary", ""),
                    implementation_details=last_raw.get("implementation_details", ""),
                    testing_performed=last_raw.get("testing_performed", ""),
                    pull_request_url=last_raw.get("pull_request_url"),
                    deliverable_url=last_raw.get("deliverable_url"),
                    additional_comments=last_raw.get("additional_comments"),
                    submission_number=last_raw.get("submission_number", 1),
                    status=SubmissionStatusEnum(last_raw.get("status", "PENDING")),
                    created_at=datetime.fromisoformat(last_raw["created_at"].replace("Z", "+00:00")),
                    attachments=latest_atts,
                )

            pm_user = proj_data.get("users") or {}
            pm_name = pm_user.get("name") if isinstance(pm_user, dict) else None

            results.append(
                PendingReviewTaskOut(
                    task_id=task_id,
                    task_title=r.get("title", ""),
                    task_description=r.get("description"),
                    acceptance_criteria=r.get("acceptance_criteria"),
                    priority=r.get("priority", "MEDIUM"),
                    due_date=r.get("due_date"),
                    status=r.get("status", "REVIEW"),
                    project_id=str(r["project_id"]) if r.get("project_id") else None,
                    project_name=proj_data.get("name"),
                    project_manager_id=str(proj_data.get("project_manager_id")) if proj_data.get("project_manager_id") else None,
                    project_manager_name=pm_name,
                    developer_id=str(r["assigned_to"]) if r.get("assigned_to") else None,
                    developer_name=dev_data.get("name"),
                    developer_email=dev_data.get("email"),
                    submission_count=len(submissions_raw),
                    latest_submission=latest_sub,
                    submitted_at=latest_sub.created_at if latest_sub else r.get("review_started_at"),
                )
            )

        return results
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while listing pending reviews: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# 3. Submission & Review History Retrieval
# ---------------------------------------------------------------------------

def get_task_submissions_service(
    task_id: str,
    current_user: UserOut,
) -> List[TaskSubmissionOut]:
    """
    Retrieve full submission and review history for a task.
    Authorizations:
    - Assigned developer
    - Project manager of the project
    - Admin
    """
    # Verify task exists & check access
    try:
        resp = _db().table("tasks").select("*, projects(id, name, project_manager_id)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        task_row = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error finding task: {str(exc)}",
        )

    project_info = task_row.get("projects") or {}
    if isinstance(project_info, list) and len(project_info) > 0:
        project_info = project_info[0]
    elif isinstance(project_info, list):
        project_info = {}

    # Authorization
    if current_user.role.value == RoleEnum.DEVELOPER.value:
        if str(task_row.get("assigned_to")) != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view submissions for this task.",
            )
    elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
        pm_id = str(project_info.get("project_manager_id")) if project_info.get("project_manager_id") else None
        if pm_id != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view submissions outside your managed projects.",
            )

    # Fetch submissions, reviews, and attachments
    raw_subs = _fetch_task_submissions_raw(task_id)
    raw_revs = _fetch_task_reviews_raw(task_id)
    raw_atts = _fetch_task_attachments_raw(task_id)

    # Collect user IDs to batch fetch names
    user_ids = []
    for s in raw_subs:
        if s.get("submitted_by"):
            user_ids.append(str(s["submitted_by"]))
    for r in raw_revs:
        if r.get("reviewer_id"):
            user_ids.append(str(r["reviewer_id"]))
    for a in raw_atts:
        if a.get("uploaded_by"):
            user_ids.append(str(a["uploaded_by"]))

    users_map = _batch_fetch_users(user_ids)

    # Map reviews by submission_id
    reviews_by_sub: Dict[str, List[TaskReviewOut]] = {}
    for r in raw_revs:
        sub_id = str(r.get("submission_id", ""))
        rev_user = users_map.get(str(r.get("reviewer_id")), {})
        rev_obj = TaskReviewOut(
            id=str(r["id"]),
            task_id=task_id,
            submission_id=sub_id,
            reviewer_id=str(r.get("reviewer_id", "")),
            reviewer_name=rev_user.get("name"),
            reviewer_email=rev_user.get("email"),
            decision=ReviewDecisionEnum(r.get("decision", "APPROVE")),
            review_comment=r.get("review_comment"),
            created_at=datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")),
        )
        reviews_by_sub.setdefault(sub_id, []).append(rev_obj)

    # Map attachments by submission_id
    attachments_by_sub: Dict[str, List[TaskAttachmentOut]] = {}
    for a in raw_atts:
        sub_id = str(a.get("submission_id", ""))
        att_user = users_map.get(str(a.get("uploaded_by")), {})
        att_obj = TaskAttachmentOut(
            id=str(a["id"]),
            task_id=task_id,
            submission_id=sub_id,
            uploaded_by=str(a.get("uploaded_by", "")),
            uploader_name=att_user.get("name"),
            filename=a.get("filename", "attachment"),
            storage_path=a.get("storage_path", ""),
            mime_type=a.get("mime_type", "application/octet-stream"),
            size=int(a.get("size", 0)),
            created_at=datetime.fromisoformat(str(a["created_at"]).replace("Z", "+00:00")),
            preview_url=f"/api/v1/tasks/{task_id}/attachments/{a['id']}/preview",
            download_url=f"/api/v1/tasks/{task_id}/attachments/{a['id']}/download",
        )
        attachments_by_sub.setdefault(sub_id, []).append(att_obj)

    results: List[TaskSubmissionOut] = []
    for s in raw_subs:
        sub_id = str(s["id"])
        sub_user = users_map.get(str(s.get("submitted_by")), {})
        sub_obj = TaskSubmissionOut(
            id=sub_id,
            task_id=task_id,
            submitted_by=str(s.get("submitted_by", "")),
            submitter_name=sub_user.get("name"),
            submitter_email=sub_user.get("email"),
            work_summary=s.get("work_summary", ""),
            implementation_details=s.get("implementation_details", ""),
            testing_performed=s.get("testing_performed", ""),
            pull_request_url=s.get("pull_request_url"),
            deliverable_url=s.get("deliverable_url"),
            additional_comments=s.get("additional_comments"),
            submission_number=s.get("submission_number", 1),
            status=SubmissionStatusEnum(s.get("status", "PENDING")),
            created_at=datetime.fromisoformat(str(s["created_at"]).replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(str(s["updated_at"]).replace("Z", "+00:00")) if s.get("updated_at") else None,
            reviews=reviews_by_sub.get(sub_id, []),
            attachments=attachments_by_sub.get(sub_id, []),
        )
        results.append(sub_obj)

    return results


# ---------------------------------------------------------------------------
# 4. Project Manager Review Actions (APPROVE, REQUEST_CHANGES, REJECT)
# ---------------------------------------------------------------------------

def review_task_service(
    task_id: str,
    payload: TaskReviewActionRequest,
    current_user: UserOut,
) -> dict:
    """
    Apply a Project Manager review decision to a task in REVIEW status.
    1. APPROVE:
       - Task status becomes COMPLETED.
       - completed_at is set to now().
       - Triggers project progress recalculation.
       - Dispatches TASK_COMPLETED notification to developer.
    2. REQUEST_CHANGES:
       - Requires specific feedback comment.
       - Task status returns to IN_PROGRESS.
       - Dispatches notification with feedback.
       - Preserves submission and review history for resubmission.
    3. REJECT:
       - Requires rejection reason comment.
       - Task status moves to IN_PROGRESS (with visible rejection & correction path).
       - Dispatches notification with rejection reason.
       - Preserves submission and review history.
    """
    # 1. RBAC: Prevent developers from approving or reviewing
    if current_user.role.value == RoleEnum.DEVELOPER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developers are not permitted to review or approve tasks.",
        )

    # 2. Fetch task with project details
    try:
        resp = _db().table("tasks").select("*, projects(id, name, project_manager_id)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found.",
            )
        task_row = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error looking up task: {str(exc)}",
        )

    project_info = task_row.get("projects") or {}
    if isinstance(project_info, list) and len(project_info) > 0:
        project_info = project_info[0]
    elif isinstance(project_info, list):
        project_info = {}

    project_id = str(task_row.get("project_id")) if task_row.get("project_id") else None
    pm_id = str(project_info.get("project_manager_id")) if project_info.get("project_manager_id") else None
    project_name = project_info.get("name") or "Project"

    # Verify PM manages this project
    if current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
        if pm_id != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to review tasks outside your managed projects.",
            )

    # 3. Verify task is in REVIEW status
    current_status = (task_row.get("status") or "TODO").upper()
    if current_status != "REVIEW":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task is in '{current_status}' status. Only tasks in 'REVIEW' status can be reviewed.",
        )

    # 4. Find latest submission to associate review
    submissions = _fetch_task_submissions_raw(task_id)
    latest_sub = submissions[-1] if submissions else None
    submission_id = str(latest_sub["id"]) if latest_sub else str(uuid.uuid4())

    now_iso = datetime.now(timezone.utc).isoformat()
    decision = payload.decision.value

    # 5. Record the review
    review_record = {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "submission_id": submission_id,
        "reviewer_id": str(current_user.id),
        "decision": decision,
        "review_comment": payload.review_comment.strip() if payload.review_comment else None,
        "created_at": now_iso,
    }
    saved_review = _save_review_record(review_record)

    assigned_dev_id = str(task_row.get("assigned_to")) if task_row.get("assigned_to") else None
    task_title = task_row.get("title", "Task")

    # 6. Apply state transition based on decision
    if payload.decision == ReviewDecisionEnum.APPROVE:
        new_task_status = TaskStatus.COMPLETED.value
        new_sub_status = SubmissionStatusEnum.APPROVED.value
        task_update = {
            "status": new_task_status,
            "completed_at": now_iso,
            "updated_at": now_iso,
        }
        activity_action = "TASK_APPROVED"
        activity_desc = f"Task '{task_title}' was approved and marked COMPLETED by {current_user.name}"
        notif_type = NotificationType.TASK_COMPLETED
        notif_title = "Task Approved"
        notif_msg = f'Your task "{task_title}" in project "{project_name}" has been approved and completed!'
        if payload.review_comment:
            notif_msg += f' Feedback: "{payload.review_comment.strip()}"'

    elif payload.decision == ReviewDecisionEnum.REQUEST_CHANGES:
        new_task_status = TaskStatus.IN_PROGRESS.value
        new_sub_status = SubmissionStatusEnum.CHANGES_REQUESTED.value
        task_update = {
            "status": new_task_status,
            "updated_at": now_iso,
        }
        activity_action = "TASK_CHANGES_REQUESTED"
        activity_desc = f"Changes requested for task '{task_title}' by {current_user.name}"
        notif_type = NotificationType.TASK_STATUS_CHANGED
        notif_title = "Task Review: Changes Requested"
        notif_msg = f'Changes were requested on task "{task_title}" in project "{project_name}": {payload.review_comment}'

    elif payload.decision == ReviewDecisionEnum.REJECT:
        new_task_status = TaskStatus.IN_PROGRESS.value
        new_sub_status = SubmissionStatusEnum.REJECTED.value
        task_update = {
            "status": new_task_status,
            "updated_at": now_iso,
        }
        activity_action = "TASK_REJECTED"
        activity_desc = f"Submission for task '{task_title}' was rejected by {current_user.name}"
        notif_type = NotificationType.TASK_STATUS_CHANGED
        notif_title = "Task Submission Rejected"
        notif_msg = f'Submission for task "{task_title}" was rejected: {payload.review_comment}. Please review feedback and resubmit.'

    # Update submission status
    if latest_sub:
        _update_submission_status(task_id, submission_id, new_sub_status)

    # Update task in DB
    try:
        _db().table("tasks").update(task_update).eq("id", task_id).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error updating task status: {str(exc)}",
        )

    # Recalculate project progress & project status
    if project_id:
        _recalculate_project_progress(project_id)
        _recalculate_project_status(project_id)

    # Activity log
    log_activity(
        user_id=current_user.id,
        action=activity_action,
        entity_type="task",
        entity_id=task_id,
        project_id=project_id,
        description=activity_desc,
        metadata={
            "submission_id": submission_id,
            "decision": decision,
            "review_comment": payload.review_comment,
            "previous_status": current_status,
            "new_status": new_task_status,
        }
    )

    # Notify assigned developer
    if assigned_dev_id and assigned_dev_id != str(current_user.id):
        try:
            NotificationService.create_notification(
                recipient_id=assigned_dev_id,
                type=notif_type,
                title=notif_title,
                message=notif_msg,
                project_id=project_id,
                task_id=task_id,
            )
        except Exception as notif_err:
            print(f"Notice: Failed to dispatch developer review notification: {notif_err}")

    # Refetch updated task
    updated_task_resp = (
        _db()
        .table("tasks")
        .select("*, projects(name), users(name)")
        .eq("id", task_id)
        .limit(1)
        .execute()
    )
    updated_task = _row_to_task_out(updated_task_resp.data[0]) if updated_task_resp.data else None

    return {
        "message": f"Task review submitted successfully with decision: {decision}.",
        "task": updated_task,
        "review": TaskReviewOut(
            id=saved_review["id"],
            task_id=task_id,
            submission_id=submission_id,
            reviewer_id=str(current_user.id),
            reviewer_name=current_user.name,
            reviewer_email=current_user.email,
            decision=ReviewDecisionEnum(decision),
            review_comment=payload.review_comment,
            created_at=datetime.fromisoformat(saved_review["created_at"].replace("Z", "+00:00")),
        ),
    }


# ---------------------------------------------------------------------------
# 5. GitHub Evidence Integration
# ---------------------------------------------------------------------------

async def get_task_github_evidence_service(
    task_id: str,
    current_user: UserOut,
) -> GitHubEvidenceOut:
    """
    Retrieve real GitHub evidence for a task submission if a PR URL is provided.
    Extracts repository, PR status, files changed, additions/deletions, and review state.
    Never fabricates code changes or exposes tokens.
    """
    # 1. Fetch task
    try:
        resp = _db().table("tasks").select("*, projects(id, name, project_manager_id)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Task not found.")
        task_row = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")

    # 2. Check access
    proj_info = task_row.get("projects") or {}
    if isinstance(proj_info, list) and len(proj_info) > 0:
        proj_info = proj_info[0]
    elif isinstance(proj_info, list):
        proj_info = {}

    if current_user.role.value == RoleEnum.DEVELOPER.value:
        if str(task_row.get("assigned_to")) != str(current_user.id):
            raise HTTPException(status_code=403, detail="Forbidden.")
    elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
        pm_id = str(proj_info.get("project_manager_id")) if proj_info.get("project_manager_id") else None
        if pm_id != str(current_user.id):
            raise HTTPException(status_code=403, detail="Forbidden.")

    # 3. Find PR URL from latest submission, or fallback to activity log association
    pr_url = None
    subs = _fetch_task_submissions_raw(task_id)
    if subs:
        for s in reversed(subs):
            if s.get("pull_request_url"):
                pr_url = s.get("pull_request_url")
                break

    if not pr_url:
        # Check task PR link in activity_logs
        try:
            logs = _db().table("activity_logs").select("metadata").eq("entity_id", task_id).eq("action", "TASK_PR_LINKED").order("created_at", desc=True).limit(1).execute()
            if logs.data:
                pr_url = (logs.data[0].get("metadata") or {}).get("pr_url")
        except Exception:
            pass

    if not pr_url:
        return GitHubEvidenceOut(
            available=False,
            message="No GitHub pull request URL was provided for this task.",
        )

    # 4. Parse GitHub PR URL
    # Format: https://github.com/{owner}/{repo}/pull/{pr_number}
    match = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url.strip())
    if not match:
        return GitHubEvidenceOut(
            available=False,
            pull_request_url=pr_url,
            message="Provided URL is not a recognized GitHub pull request link.",
        )

    owner = match.group(1)
    repo_name = match.group(2).removesuffix(".git")
    pr_number = int(match.group(3))
    full_repo = f"{owner}/{repo_name}"

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AI-DevFlow-Intelligence-Suite/1.0",
    }
    if hasattr(settings, "GITHUB_TOKEN") and settings.GITHUB_TOKEN and settings.GITHUB_TOKEN != "your-github-token-here":
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=10.0) as client:
        try:
            # 1. Fetch PR details
            pr_res = await client.get(f"https://api.github.com/repos/{full_repo}/pulls/{pr_number}")
            if pr_res.status_code == 404:
                return GitHubEvidenceOut(
                    available=False,
                    pull_request_url=pr_url,
                    repository_name=full_repo,
                    pull_request_number=pr_number,
                    message=f"Pull request #{pr_number} not found on public repository {full_repo}.",
                )
            if pr_res.status_code in (401, 403):
                # Rate limited or private repository
                return GitHubEvidenceOut(
                    available=False,
                    pull_request_url=pr_url,
                    repository_name=full_repo,
                    pull_request_number=pr_number,
                    message="GitHub API rate limit reached or repository is private. You can view the pull request directly via the provided link.",
                )

            pr_res.raise_for_status()
            pr_data = pr_res.json()

            # 2. Fetch changed files
            files_changed = []
            try:
                files_res = await client.get(f"https://api.github.com/repos/{full_repo}/pulls/{pr_number}/files?per_page=30")
                if files_res.status_code == 200:
                    for f in files_res.json():
                        files_changed.append({
                            "filename": f.get("filename"),
                            "status": f.get("status"),
                            "additions": f.get("additions", 0),
                            "deletions": f.get("deletions", 0),
                            "changes": f.get("changes", 0),
                        })
            except Exception:
                pass

            # 3. Fetch reviews
            reviews_list = []
            try:
                reviews_res = await client.get(f"https://api.github.com/repos/{full_repo}/pulls/{pr_number}/reviews?per_page=10")
                if reviews_res.status_code == 200:
                    for rev in reviews_res.json():
                        reviews_list.append({
                            "reviewer": rev.get("user", {}).get("login"),
                            "state": rev.get("state"),
                            "submitted_at": rev.get("submitted_at"),
                        })
            except Exception:
                pass

            return GitHubEvidenceOut(
                available=True,
                repository_name=full_repo,
                pull_request_url=pr_data.get("html_url") or pr_url,
                pull_request_number=pr_number,
                title=pr_data.get("title"),
                status=pr_data.get("state"),
                is_merged=pr_data.get("merged", False),
                author=pr_data.get("user", {}).get("login"),
                additions=pr_data.get("additions"),
                deletions=pr_data.get("deletions"),
                changed_files_count=pr_data.get("changed_files"),
                changed_files=files_changed,
                reviews=reviews_list,
            )

        except Exception as exc:
            return GitHubEvidenceOut(
                available=False,
                pull_request_url=pr_url,
                repository_name=full_repo,
                pull_request_number=pr_number,
                message=f"GitHub API connection notice: {str(exc)}. Please access pull request directly via link.",
            )


# ---------------------------------------------------------------------------
# 6. Attachment Preview & Download Service
# ---------------------------------------------------------------------------

def get_attachment_stream_service(
    task_id: str,
    attachment_id: str,
    current_user: UserOut,
) -> tuple[bytes, str, str]:
    """
    Authorizes and streams an attachment directly from private Supabase Storage.
    Returns (file_bytes, filename, mime_type).
    """
    # 1. Fetch task & verify access
    try:
        resp = _db().table("tasks").select("*, projects(project_manager_id)").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
        task_row = resp.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {exc}")

    proj_info = task_row.get("projects") or {}
    if isinstance(proj_info, list) and len(proj_info) > 0:
        proj_info = proj_info[0]
    elif isinstance(proj_info, list):
        proj_info = {}

    if current_user.role.value == RoleEnum.DEVELOPER.value:
        if str(task_row.get("assigned_to")) != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: You cannot access attachments for this task.",
            )
    elif current_user.role.value == RoleEnum.PROJECT_MANAGER.value:
        pm_id = str(proj_info.get("project_manager_id")) if proj_info.get("project_manager_id") else None
        if pm_id != str(current_user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: You cannot access attachments outside your projects.",
            )

    # 2. Find attachment record
    raw_atts = _fetch_task_attachments_raw(task_id)
    att = next((a for a in raw_atts if str(a.get("id")) == str(attachment_id)), None)
    if not att:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found.")

    storage_path = att.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment storage path missing.")

    # 3. Download bytes from private Supabase Storage bucket
    try:
        file_bytes = _db().storage.from_(ATTACHMENT_BUCKET).download(storage_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment file not found in storage: {exc}",
        )

    return file_bytes, att.get("filename", "download"), att.get("mime_type", "application/octet-stream")


def get_attachment_signed_url_service(
    task_id: str,
    attachment_id: str,
    current_user: UserOut,
) -> dict:
    """Generate a temporary signed URL for an attachment in private storage."""
    file_bytes, filename, mime_type = get_attachment_stream_service(task_id, attachment_id, current_user)
    raw_atts = _fetch_task_attachments_raw(task_id)
    att = next(a for a in raw_atts if str(a.get("id")) == str(attachment_id))
    storage_path = att.get("storage_path")

    signed_url = None
    try:
        res = _db().storage.from_(ATTACHMENT_BUCKET).create_signed_url(storage_path, 900)
        signed_url = res.get("signedURL") or res.get("signedUrl")
    except Exception:
        pass

    return {
        "id": attachment_id,
        "task_id": task_id,
        "filename": filename,
        "mime_type": mime_type,
        "signed_url": signed_url,
        "preview_url": f"/api/v1/tasks/{task_id}/attachments/{attachment_id}/preview",
        "download_url": f"/api/v1/tasks/{task_id}/attachments/{attachment_id}/download",
    }
