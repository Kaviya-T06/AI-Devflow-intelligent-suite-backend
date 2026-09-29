"""
Repositories router — placeholder endpoint.
No database connection. Returns mock responses for Swagger testing.
"""
from typing import List

from fastapi import APIRouter

from app.schemas.common import RepositoryOut

router = APIRouter(prefix="/repositories", tags=["Repositories"])

# ---------------------------------------------------------------------------
# Placeholder data
# ---------------------------------------------------------------------------

_PLACEHOLDER_REPOS: List[RepositoryOut] = [
    RepositoryOut(
        id="repo-001",
        name="ai-devflow-backend",
        full_name="org/ai-devflow-backend",
        description="FastAPI backend for the AI DevFlow Intelligence Suite.",
        url="https://github.com/org/ai-devflow-backend",
        language="Python",
        stars=42,
        open_issues=7,
        last_pushed_at="2026-09-28T18:00:00Z",
        project_id="proj-001",
    ),
    RepositoryOut(
        id="repo-002",
        name="ai-devflow-frontend",
        full_name="org/ai-devflow-frontend",
        description="React + TypeScript frontend for the AI DevFlow Intelligence Suite.",
        url="https://github.com/org/ai-devflow-frontend",
        language="TypeScript",
        stars=38,
        open_issues=4,
        last_pushed_at="2026-09-28T20:00:00Z",
        project_id="proj-001",
    ),
    RepositoryOut(
        id="repo-003",
        name="mobile-companion",
        full_name="org/mobile-companion",
        description="React Native mobile app for DevFlow.",
        url="https://github.com/org/mobile-companion",
        language="TypeScript",
        stars=12,
        open_issues=2,
        last_pushed_at="2026-09-27T11:00:00Z",
        project_id="proj-002",
    ),
]


@router.get(
    "",
    response_model=List[RepositoryOut],
    summary="List connected repositories",
)
async def list_repositories():
    """
    Retrieve all GitHub repositories connected to the platform.

    Returns repository metadata including **name**, **language**, **stars**,
    **open issues**, and **last push date**.

    > **Note:** Returns placeholder data. GitHub integration will be connected
    in a future milestone.
    """
    return _PLACEHOLDER_REPOS
