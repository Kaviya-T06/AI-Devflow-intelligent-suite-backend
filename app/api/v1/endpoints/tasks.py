"""
Tasks router — strictly interacts with public.tasks via task_service and task_submission_service.
Enforces Role-Based Access Control, automatic timestamping, task submissions, and PM reviews.
"""
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, Response, status

from app.api.deps import get_current_user
import app.schemas.task
from app.schemas.task import TaskOut, TaskUpdateRequest
from app.schemas.task_submission import (
    GitHubEvidenceOut,
    PendingReviewTaskOut,
    ReviewDecisionEnum,
    TaskAttachmentOut,
    TaskReviewActionRequest,
    TaskReviewCommentRequest,
    TaskReviewOut,
    TaskSubmissionCreateRequest,
    TaskSubmissionOut,
)
from app.schemas.user import UserOut
from app.services.task_service import (
    create_task_service,
    delete_task_service,
    get_task_by_id_service,
    list_tasks_service,
    update_task_status_service,
)
from app.services.task_submission_service import (
    get_attachment_signed_url_service,
    get_attachment_stream_service,
    get_task_github_evidence_service,
    get_task_submissions_service,
    list_pending_reviews_service,
    review_task_service,
    submit_task_for_review_service,
)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


# ---------------------------------------------------------------------------
# Task Queries & Listings
# ---------------------------------------------------------------------------

@router.get("", response_model=List[TaskOut], summary="List all tasks")
async def list_tasks(
    project_id: Optional[str] = Query(None, description="Filter tasks by project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> List[TaskOut]:
    """
    Retrieve tasks from the database:
    - Developer: View their own assigned tasks.
    - Project Manager: View tasks for managed projects (optionally filtered by project_id).
    - Admin: View all tasks (optionally filtered by project_id).
    """
    return list_tasks_service(current_user=current_user, project_id=project_id)


@router.get("/pending-reviews", response_model=List[PendingReviewTaskOut], summary="List pending task reviews")
async def list_pending_reviews(
    project_id: Optional[str] = Query(None, description="Optional project UUID filter"),
    current_user: UserOut = Depends(get_current_user),
) -> List[PendingReviewTaskOut]:
    """
    List tasks currently in REVIEW status for projects managed by the authenticated project manager.
    - Project Manager: Only tasks in projects they manage.
    - Admin: All pending tasks (optionally filtered by project_id).
    - Developer: Forbidden (403).
    """
    return list_pending_reviews_service(current_user=current_user, project_id=project_id)


@router.get("/{task_id}", response_model=TaskOut, summary="Get task by ID")
async def get_task(
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> TaskOut:
    """
    Retrieve a specific task by ID.
    - Developer: View only if assigned to them.
    """
    return get_task_by_id_service(
        task_id=task_id,
        current_user=current_user,
    )


@router.patch("/{task_id}", response_model=TaskOut, summary="Update task status")
@router.put("/{task_id}", response_model=TaskOut, summary="Update task status")
async def update_task(
    payload: TaskUpdateRequest,
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> TaskOut:
    """
    Update task status (e.g. TODO -> IN_PROGRESS).
    Automatically updates started_at, review_started_at, completed_at.
    - Developer: Can only update their own tasks.
    """
    return update_task_status_service(
        task_id=task_id,
        payload=payload,
        current_user=current_user,
    )


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED, summary="Create task")
async def create_task(
    payload: app.schemas.task.TaskCreateRequest,
    current_user: UserOut = Depends(get_current_user),
) -> TaskOut:
    """
    Create a new task.
    - Admin/Manager: Allowed.
    """
    return create_task_service(
        payload=payload,
        current_user=current_user,
    )


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete task")
async def delete_task(
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> None:
    """
    Delete a task.
    - Admin/Manager: Allowed.
    """
    return delete_task_service(
        task_id=task_id,
        current_user=current_user,
    )


@router.get("/{task_id}/recommendations", response_model=List[dict], summary="Get developer recommendations for task")
async def get_task_recommendations(
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> List[dict]:
    """
    Get ranked developer recommendations for this task based on skills, experience, and workload.
    """
    from app.services.matching_service import get_explained_recommendations
    return await get_explained_recommendations(task_id)


# ---------------------------------------------------------------------------
# Task Submissions & PM Reviews Workflow
# ---------------------------------------------------------------------------

@router.post("/{task_id}/submit", response_model=TaskSubmissionOut, status_code=status.HTTP_201_CREATED, summary="Submit task for review")
async def submit_task_for_review(
    request: Request,
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> TaskSubmissionOut:
    """
    Developer submits an assigned task for review:
    - Validates required fields: work_summary, implementation_details, testing_performed.
    - Optional: pull_request_url, deliverable_url, additional_comments.
    - Optional: multiple attached files (screenshots, photos, PDFs, docs up to 10 MB each, 25 MB total).
    - Changes task status to REVIEW.
    - Preserves previous submissions and review history.
    - Dispatches in-app notification to the project manager.
    """
    content_type = request.headers.get("content-type", "")
    files_to_upload = []

    if content_type.startswith("multipart/form-data"):
        form_data = await request.form()
        work_summary = form_data.get("work_summary")
        implementation_details = form_data.get("implementation_details")
        testing_performed = form_data.get("testing_performed")
        pull_request_url = form_data.get("pull_request_url")
        deliverable_url = form_data.get("deliverable_url")
        additional_comments = form_data.get("additional_comments")

        raw_files = form_data.getlist("files")
        for item in raw_files:
            if hasattr(item, "filename") and item.filename:
                files_to_upload.append(item)

        payload = TaskSubmissionCreateRequest(
            work_summary=str(work_summary or ""),
            implementation_details=str(implementation_details or ""),
            testing_performed=str(testing_performed or ""),
            pull_request_url=str(pull_request_url) if pull_request_url else None,
            deliverable_url=str(deliverable_url) if deliverable_url else None,
            additional_comments=str(additional_comments) if additional_comments else None,
        )
    else:
        body_json = await request.json()
        payload = TaskSubmissionCreateRequest(**body_json)

    return await submit_task_for_review_service(
        task_id=task_id,
        payload=payload,
        current_user=current_user,
        files=files_to_upload,
    )


@router.get("/{task_id}/submissions", response_model=List[TaskSubmissionOut], summary="Get task submission and review history")
async def get_task_submissions(
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> List[TaskSubmissionOut]:
    """
    Retrieve full submission and review history for a task.
    - Developer: Can view only for their assigned tasks.
    - Project Manager: Can view for tasks in managed projects.
    - Admin: Can view for all tasks.
    """
    return get_task_submissions_service(
        task_id=task_id,
        current_user=current_user,
    )


@router.post("/{task_id}/review", summary="Review task submission (Approve / Request Changes / Reject)")
async def review_task(
    payload: TaskReviewActionRequest,
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
):
    """
    Project Manager reviews a submitted task:
    - APPROVE: Changes status to COMPLETED, records reviewer, recalculates project progress.
    - REQUEST_CHANGES: Changes status to IN_PROGRESS, feedback required, notifies developer.
    - REJECT: Changes status to IN_PROGRESS, rejection reason required, notifies developer.
    - Developers are strictly forbidden from reviewing or approving tasks.
    """
    return review_task_service(
        task_id=task_id,
        payload=payload,
        current_user=current_user,
    )


@router.post("/{task_id}/reviews/approve", summary="Approve task submission")
async def approve_task_review(
    task_id: str = Path(..., description="Task UUID"),
    body: Optional[TaskReviewCommentRequest] = Body(None),
    current_user: UserOut = Depends(get_current_user),
):
    """Convenience endpoint to approve task."""
    comment = body.review_comment.strip() if (body and body.review_comment and body.review_comment.strip()) else None
    return review_task_service(
        task_id=task_id,
        payload=TaskReviewActionRequest(decision=ReviewDecisionEnum.APPROVE, review_comment=comment),
        current_user=current_user,
    )


@router.post("/{task_id}/reviews/request-changes", summary="Request changes on task submission")
async def request_changes_task_review(
    task_id: str = Path(..., description="Task UUID"),
    body: TaskReviewCommentRequest = Body(...),
    current_user: UserOut = Depends(get_current_user),
):
    """Convenience endpoint to request changes on task."""
    if not body or not body.review_comment or not body.review_comment.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A review comment is required when requesting changes.",
        )
    return review_task_service(
        task_id=task_id,
        payload=TaskReviewActionRequest(decision=ReviewDecisionEnum.REQUEST_CHANGES, review_comment=body.review_comment.strip()),
        current_user=current_user,
    )


@router.post("/{task_id}/reviews/reject", summary="Reject task submission")
async def reject_task_review(
    task_id: str = Path(..., description="Task UUID"),
    body: TaskReviewCommentRequest = Body(...),
    current_user: UserOut = Depends(get_current_user),
):
    """Convenience endpoint to reject task submission."""
    if not body or not body.review_comment or not body.review_comment.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A review comment is required when rejecting a submission.",
        )
    return review_task_service(
        task_id=task_id,
        payload=TaskReviewActionRequest(decision=ReviewDecisionEnum.REJECT, review_comment=body.review_comment.strip()),
        current_user=current_user,
    )


@router.get("/{task_id}/github-evidence", response_model=GitHubEvidenceOut, summary="Get GitHub PR evidence for task")
async def get_task_github_evidence(
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> GitHubEvidenceOut:
    """
    Fetch real GitHub pull request evidence (repo name, PR title & status, changed files, additions/deletions, review state)
    for the specified task.
    """
    return await get_task_github_evidence_service(
        task_id=task_id,
        current_user=current_user,
    )


# ---------------------------------------------------------------------------
# Attachment Access (private — streamed via backend, never direct Storage URLs)
# ---------------------------------------------------------------------------

@router.get(
    "/{task_id}/attachments/{attachment_id}/preview",
    summary="Preview or stream an attachment (authorized users only)",
)
async def preview_task_attachment(
    task_id: str = Path(..., description="Task UUID"),
    attachment_id: str = Path(..., description="Attachment UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> Response:
    """
    Stream an attachment for inline preview. Images and PDFs render inline;
    other types force a browser download. All access is JWT-authenticated.
    """
    file_bytes, filename, mime_type = get_attachment_stream_service(
        task_id=task_id,
        attachment_id=attachment_id,
        current_user=current_user,
    )
    disposition = (
        "inline"
        if mime_type.startswith("image/") or mime_type == "application/pdf"
        else "attachment"
    )
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get(
    "/{task_id}/attachments/{attachment_id}/download",
    summary="Force-download an attachment (authorized users only)",
)
async def download_task_attachment(
    task_id: str = Path(..., description="Task UUID"),
    attachment_id: str = Path(..., description="Attachment UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> Response:
    """
    Force-download an attachment from private Supabase Storage.
    Access is JWT-authenticated; the file is never publicly exposed.
    """
    file_bytes, filename, mime_type = get_attachment_stream_service(
        task_id=task_id,
        attachment_id=attachment_id,
        current_user=current_user,
    )
    return Response(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
