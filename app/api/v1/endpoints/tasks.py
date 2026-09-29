"""
Tasks router — placeholder endpoints.
No database connection. Returns mock responses for Swagger testing.
"""
from typing import List

from fastapi import APIRouter, Path

from app.schemas.task import TaskCreateRequest, TaskOut, TaskPriority, TaskStatus

router = APIRouter(prefix="/tasks", tags=["Tasks"])

# ---------------------------------------------------------------------------
# Placeholder data
# ---------------------------------------------------------------------------

_PLACEHOLDER_TASKS: List[TaskOut] = [
    TaskOut(
        id="task-001",
        title="Design database schema",
        description="Create ERD and define all tables for the platform.",
        status=TaskStatus.DONE,
        priority=TaskPriority.HIGH,
        project_id="proj-001",
        assignee_id="user-002",
        due_date="2026-09-30",
    ),
    TaskOut(
        id="task-002",
        title="Implement FastAPI routers",
        description="Build all REST API endpoints under /api/v1.",
        status=TaskStatus.IN_PROGRESS,
        priority=TaskPriority.HIGH,
        project_id="proj-001",
        assignee_id="user-002",
        due_date="2026-10-05",
    ),
    TaskOut(
        id="task-003",
        title="Build Admin Dashboard",
        description="Create the admin panel with user and project management.",
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        project_id="proj-001",
        assignee_id="user-003",
        due_date="2026-10-20",
    ),
    TaskOut(
        id="task-004",
        title="Write integration tests",
        description="Cover all API endpoints with pytest integration tests.",
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        project_id="proj-001",
        assignee_id="user-004",
        due_date="2026-10-25",
    ),
    TaskOut(
        id="task-005",
        title="Deploy to production",
        description="Set up CI/CD and deploy the application.",
        status=TaskStatus.BLOCKED,
        priority=TaskPriority.CRITICAL,
        project_id="proj-001",
        due_date="2026-11-01",
    ),
]


@router.get(
    "",
    response_model=List[TaskOut],
    summary="List all tasks",
)
async def list_tasks():
    """
    Retrieve all tasks across all projects.

    Returns task details including title, status, priority, assignee, and due date.

    > **Note:** Returns placeholder data. Database integration coming in a future milestone.
    """
    return _PLACEHOLDER_TASKS


@router.get(
    "/{task_id}",
    response_model=TaskOut,
    summary="Get a single task by ID",
)
async def get_task(
    task_id: str = Path(..., description="The unique identifier of the task"),
):
    """
    Retrieve the full details of a specific task by its **task_id**.

    > **Note:** Returns placeholder data. Database integration coming in a future milestone.
    """
    return TaskOut(
        id=task_id,
        title="Placeholder Task",
        description="This is a placeholder task returned for Swagger testing.",
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
        project_id="proj-001",
    )


@router.post(
    "",
    response_model=TaskOut,
    summary="Create a new task",
    status_code=201,
)
async def create_task(payload: TaskCreateRequest):
    """
    Create a new task and optionally assign it to a project and team member.

    Requires **title** at minimum. Optionally set description, status, priority,
    project, assignee, and due date.

    > **Note:** This is a placeholder — no task is persisted yet.
    """
    return TaskOut(
        id="task-new-placeholder",
        title=payload.title,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        project_id=payload.project_id,
        assignee_id=payload.assignee_id,
        due_date=payload.due_date,
    )
