"""
Projects router — placeholder endpoints.
No database connection. Returns mock responses for Swagger testing.
"""
from typing import List

from fastapi import APIRouter, Path

from app.schemas.project import ProjectCreateRequest, ProjectOut, ProjectStatus

router = APIRouter(prefix="/projects", tags=["Projects"])

# ---------------------------------------------------------------------------
# Placeholder data
# ---------------------------------------------------------------------------

_PLACEHOLDER_PROJECTS: List[ProjectOut] = [
    ProjectOut(
        id="proj-001",
        name="AI DevFlow Intelligence Suite",
        description="Full-stack developer workflow intelligence platform.",
        status=ProjectStatus.ACTIVE,
        start_date="2026-01-01",
        end_date="2026-12-31",
        owner_id="user-001",
        member_count=5,
        task_count=24,
    ),
    ProjectOut(
        id="proj-002",
        name="Mobile Companion App",
        description="React Native companion app for DevFlow.",
        status=ProjectStatus.PLANNING,
        start_date="2026-10-01",
        owner_id="user-003",
        member_count=3,
        task_count=8,
    ),
    ProjectOut(
        id="proj-003",
        name="Legacy System Migration",
        description="Migrate legacy monolith to microservices.",
        status=ProjectStatus.ON_HOLD,
        owner_id="user-001",
        member_count=2,
        task_count=15,
    ),
]


@router.get(
    "",
    response_model=List[ProjectOut],
    summary="List all projects",
)
async def list_projects():
    """
    Retrieve all projects accessible to the authenticated user.

    Returns project details including status, member count, and task count.

    > **Note:** Returns placeholder data. Database integration coming in a future milestone.
    """
    return _PLACEHOLDER_PROJECTS


@router.get(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Get a single project by ID",
)
async def get_project(
    project_id: str = Path(..., description="The unique identifier of the project"),
):
    """
    Retrieve full details of a specific project by its **project_id**.

    > **Note:** Returns placeholder data. Database integration coming in a future milestone.
    """
    return ProjectOut(
        id=project_id,
        name="Placeholder Project",
        description="This is a placeholder project returned for Swagger testing.",
        status=ProjectStatus.ACTIVE,
        owner_id="user-001",
        member_count=4,
        task_count=12,
    )


@router.post(
    "",
    response_model=ProjectOut,
    summary="Create a new project",
    status_code=201,
)
async def create_project(payload: ProjectCreateRequest):
    """
    Create a new project in the system.

    Requires **name** at minimum. Optionally set description, status,
    start/end dates, and owner.

    > **Note:** This is a placeholder — no project is persisted yet.
    """
    return ProjectOut(
        id="proj-new-placeholder",
        name=payload.name,
        description=payload.description,
        status=payload.status,
        start_date=payload.start_date,
        end_date=payload.end_date,
        owner_id=payload.owner_id,
        member_count=0,
        task_count=0,
    )
