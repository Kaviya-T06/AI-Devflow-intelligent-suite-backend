"""
Projects router — Complete Project Management CRUD API.
Secured with FastAPI JWT and Role-Based Access Control against public.users.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import get_current_user
from app.schemas.project import (
    ProjectCreateRequest,
    ProjectOut,
    ProjectUpdateRequest,
)
from app.schemas.user import UserOut
from app.services.project_service import (
    create_project_service,
    delete_project_service,
    get_project_by_id_service,
    get_project_history_service,
    list_projects_service,
    update_project_service,
)
from app.schemas.common import ActivityOut

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=List[ProjectOut], summary="List projects")
async def list_projects(
    status: Optional[str] = Query(None, description="Filter by project status (planning, active, on_hold, completed, archived)"),
    project_manager_id: Optional[str] = Query(None, description="Filter by project manager UUID"),
    search: Optional[str] = Query(None, description="Search term for project name"),
    current_user: UserOut = Depends(get_current_user),
) -> List[ProjectOut]:
    """
    List projects from the database:
    - Admin: View all projects with optional filtering.
    - Project Manager: View projects they manage.
    - Developer: View platform projects.
    """
    return list_projects_service(
        current_user=current_user,
        status_filter=status,
        pm_filter=project_manager_id,
        search=search,
    )

# GET /projects/{project_id} — Single project
# ---------------------------------------------------------------------------

@router.get("/{project_id}", response_model=ProjectOut, summary="Get project by ID")
async def get_project(
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> ProjectOut:
    """
    Retrieve details for a specific project.
    """
    return get_project_by_id_service(
        project_id=project_id,
        current_user=current_user,
    )


@router.get("/{project_id}/history", response_model=List[ActivityOut], summary="Get project history")
async def get_project_history(
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> List[ActivityOut]:
    """
    Retrieve activity log history for a specific project.
    Enforces RBAC: Must be Admin or Project Manager of this project.
    """
    return get_project_history_service(
        project_id=project_id,
        current_user=current_user,
    )


@router.post(
    "",
    response_model=ProjectOut,
    summary="Create a new project",
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: ProjectCreateRequest,
    current_user: UserOut = Depends(get_current_user),
) -> ProjectOut:
    """
    Create a new project in the database.
    - Admin: Can create projects and assign any valid active PM.
    - Project Manager: Can create projects for themselves or team.
    - Developer: Forbidden (403).
    """
    return create_project_service(
        payload=payload,
        current_user=current_user,
    )


@router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Update project details",
)
@router.put(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Update project details",
)
async def update_project(
    payload: ProjectUpdateRequest,
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> ProjectOut:
    """
    Partially update project attributes:
    - Admin: Can edit any project, change PM, status, progress, dates.
    - Project Manager: Can only edit projects they manage. Cannot reassign PM to another user.
    - Developer: Forbidden (403).
    """
    return update_project_service(
        project_id=project_id,
        payload=payload,
        current_user=current_user,
    )


@router.delete(
    "/{project_id}",
    response_model=ProjectOut,
    summary="Archive / delete project",
)
async def delete_project(
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> ProjectOut:
    """
    Safely archive a project (sets status = 'archived'):
    - Admin: Can archive any project.
    - Project Manager: Can archive projects they manage.
    - Developer: Forbidden (403).
    """
    return delete_project_service(
        project_id=project_id,
        current_user=current_user,
    )


from app.schemas.simulation import (
    DeadlineSimulationRequest,
    DeadlineSimulationResponse,
    DeveloperUnavailabilitySimulationRequest,
    UnavailabilitySimulationResponse,
    ProjectSimulatorData,
    TaskEstimateUpdateRequest,
    DeveloperCapacityUpdateRequest,
    DeveloperCapacityInfo,
)
from app.services.simulation_service import (
    simulate_deadline_change,
    simulate_developer_unavailability,
    get_project_simulator_data_service,
    update_task_estimate_service,
    update_developer_capacity_service,
)

@router.get(
    "/{project_id}/simulator-data",
    response_model=ProjectSimulatorData,
    summary="Get simulator dataset (tasks, estimates, developer capacities)",
)
async def get_project_simulator_data(
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> ProjectSimulatorData:
    """
    Fetch all data needed for the What-If Simulator:
    - Incomplete tasks and their current effort estimates
    - Assigned developers and their weekly capacities
    - Missing data indicators
    """
    return get_project_simulator_data_service(project_id, current_user)

@router.patch(
    "/{project_id}/tasks/{task_id}/estimate",
    summary="Update task estimated hours for What-If Simulator",
)
async def update_project_task_estimate(
    payload: TaskEstimateUpdateRequest,
    project_id: str = Path(..., description="Project UUID"),
    task_id: str = Path(..., description="Task UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> dict:
    """
    Allow authorized PMs or Admins to enter or update estimated hours for a task.
    """
    return update_task_estimate_service(
        project_id=project_id,
        task_id=task_id,
        estimated_hours=payload.estimated_hours,
        current_user=current_user,
    )

@router.patch(
    "/{project_id}/developers/{developer_id}/capacity",
    response_model=DeveloperCapacityInfo,
    summary="Update developer weekly capacity hours",
)
async def update_project_developer_capacity(
    payload: DeveloperCapacityUpdateRequest,
    project_id: str = Path(..., description="Project UUID"),
    developer_id: str = Path(..., description="Developer User UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> DeveloperCapacityInfo:
    """
    Allow authorized PMs or Admins to enter or update weekly available hours for an assigned developer.
    """
    return update_developer_capacity_service(
        project_id=project_id,
        developer_id=developer_id,
        capacity_hours_per_week=payload.capacity_hours_per_week,
        current_user=current_user,
    )

@router.post(
    "/{project_id}/simulate-deadline",
    response_model=DeadlineSimulationResponse,
    summary="Simulate a project deadline change",
)
async def simulate_deadline(
    payload: DeadlineSimulationRequest,
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> DeadlineSimulationResponse:
    """
    Run a deterministic What-If simulation to see how changing the deadline impacts capacity.
    Does NOT update any project data.
    """
    return await simulate_deadline_change(
        project_id=project_id,
        payload=payload,
        current_user=current_user,
    )

@router.post(
    "/{project_id}/simulate-unavailability",
    response_model=UnavailabilitySimulationResponse,
    summary="Simulate a developer's unavailability",
)
async def simulate_unavailability(
    payload: DeveloperUnavailabilitySimulationRequest,
    project_id: str = Path(..., description="Project UUID"),
    current_user: UserOut = Depends(get_current_user),
) -> UnavailabilitySimulationResponse:
    """
    Run a deterministic What-If simulation to see how a developer's absence impacts project capacity.
    Does NOT update any project data.
    """
    return await simulate_developer_unavailability(
        project_id=project_id,
        payload=payload,
        current_user=current_user,
    )


