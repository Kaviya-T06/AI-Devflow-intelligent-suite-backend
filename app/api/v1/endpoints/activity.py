"""
Activity router — placeholder endpoint.
No database connection. Returns mock responses for Swagger testing.
"""
from typing import List

from fastapi import APIRouter

from app.schemas.common import ActivityOut

router = APIRouter(prefix="/activity", tags=["Activity"])

# ---------------------------------------------------------------------------
# Placeholder data
# ---------------------------------------------------------------------------

_PLACEHOLDER_ACTIVITY: List[ActivityOut] = [
    ActivityOut(
        id="act-001",
        actor_id="user-001",
        actor_name="Alice Johnson",
        action="created",
        resource_type="project",
        resource_id="proj-001",
        resource_name="AI DevFlow Intelligence Suite",
        timestamp="2026-09-28T10:00:00Z",
    ),
    ActivityOut(
        id="act-002",
        actor_id="user-002",
        actor_name="Bob Smith",
        action="completed",
        resource_type="task",
        resource_id="task-001",
        resource_name="Design database schema",
        timestamp="2026-09-28T11:30:00Z",
    ),
    ActivityOut(
        id="act-003",
        actor_id="user-003",
        actor_name="Carol Davis",
        action="assigned",
        resource_type="task",
        resource_id="task-003",
        resource_name="Build Admin Dashboard",
        timestamp="2026-09-28T13:00:00Z",
    ),
    ActivityOut(
        id="act-004",
        actor_id="user-002",
        actor_name="Bob Smith",
        action="commented",
        resource_type="task",
        resource_id="task-002",
        resource_name="Implement FastAPI routers",
        timestamp="2026-09-28T14:45:00Z",
    ),
    ActivityOut(
        id="act-005",
        actor_id="user-001",
        actor_name="Alice Johnson",
        action="updated",
        resource_type="project",
        resource_id="proj-002",
        resource_name="Mobile Companion App",
        timestamp="2026-09-28T16:20:00Z",
    ),
]


@router.get(
    "",
    response_model=List[ActivityOut],
    summary="Get recent activity feed",
)
async def get_activity():
    """
    Retrieve a chronological feed of recent activity events across the platform.

    Each activity record includes the **actor**, **action performed**,
    the **resource** affected, and the **timestamp**.

    Useful for audit trails, team dashboards, and notification systems.

    > **Note:** Returns placeholder data. Database integration coming in a future milestone.
    """
    return _PLACEHOLDER_ACTIVITY
