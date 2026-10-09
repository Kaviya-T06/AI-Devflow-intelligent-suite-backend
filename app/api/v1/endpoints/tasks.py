"""
Tasks router — strictly interacts with public.tasks via task_service.
Enforces Role-Based Access Control and automatic timestamping.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import get_current_user
import app.schemas.task
from app.schemas.task import TaskOut, TaskUpdateRequest
from app.schemas.user import UserOut
from app.services.task_service import (
    get_task_by_id_service,
    list_tasks_service,
    update_task_status_service,
    create_task_service,
    delete_task_service,
)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


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
