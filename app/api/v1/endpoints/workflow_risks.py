"""
Workflow Risks router — placeholder endpoint.
No database connection. Returns mock responses for Swagger testing.
"""
from typing import List

from fastapi import APIRouter

from app.schemas.common import WorkflowRiskOut, RiskLevel

router = APIRouter(prefix="/workflow-risks", tags=["Workflow Risks"])

# ---------------------------------------------------------------------------
# Placeholder data
# ---------------------------------------------------------------------------

_PLACEHOLDER_RISKS: List[WorkflowRiskOut] = [
    WorkflowRiskOut(
        id="risk-001",
        title="Deployment pipeline blocked",
        description=(
            "CI/CD pipeline has been failing for 3 consecutive runs. "
            "Production release is at risk."
        ),
        level=RiskLevel.CRITICAL,
        project_id="proj-001",
        project_name="AI DevFlow Intelligence Suite",
        detected_at="2026-09-28T08:00:00Z",
        is_resolved=False,
    ),
    WorkflowRiskOut(
        id="risk-002",
        title="Multiple overdue tasks",
        description=(
            "5 tasks in the current sprint are past their due dates with no updates."
        ),
        level=RiskLevel.HIGH,
        project_id="proj-001",
        project_name="AI DevFlow Intelligence Suite",
        detected_at="2026-09-27T17:00:00Z",
        is_resolved=False,
    ),
    WorkflowRiskOut(
        id="risk-003",
        title="Low test coverage",
        description=(
            "Test coverage is currently at 34%. Recommended minimum is 80%."
        ),
        level=RiskLevel.MEDIUM,
        project_id="proj-001",
        project_name="AI DevFlow Intelligence Suite",
        detected_at="2026-09-26T12:00:00Z",
        is_resolved=False,
    ),
    WorkflowRiskOut(
        id="risk-004",
        title="Dependency vulnerability detected",
        description=(
            "A high-severity CVE was found in one of the npm dependencies. "
            "Update required."
        ),
        level=RiskLevel.HIGH,
        project_id="proj-002",
        project_name="Mobile Companion App",
        detected_at="2026-09-25T09:30:00Z",
        is_resolved=True,
    ),
]


@router.get(
    "",
    response_model=List[WorkflowRiskOut],
    summary="Get detected workflow risks",
)
async def get_workflow_risks():
    """
    Retrieve all AI-detected workflow risks across projects.

    Each risk includes a **title**, **description**, **severity level**,
    the **project** it relates to, and whether it has been **resolved**.

    Risk levels: `Low`, `Medium`, `High`, `Critical`.

    > **Note:** Returns placeholder data. AI risk detection engine will be
    connected in a future milestone.
    """
    return _PLACEHOLDER_RISKS
