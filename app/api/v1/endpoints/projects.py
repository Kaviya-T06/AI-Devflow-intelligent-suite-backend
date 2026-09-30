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
    list_projects_service,
    update_project_service,
)

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
