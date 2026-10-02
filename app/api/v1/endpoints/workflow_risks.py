"""
Workflow Risks router
"""
from typing import List
from fastapi import APIRouter, Depends

from app.schemas.common import WorkflowRiskOut
from app.schemas.user import UserOut
from app.api.deps import get_current_user
from app.services.workflow_risk_service import get_workflow_risks_service

router = APIRouter(prefix="/workflow-risks", tags=["Workflow Risks"])

@router.get("", response_model=List[WorkflowRiskOut], summary="Get detected workflow risks")
async def get_workflow_risks(
    project_id: str = None,
    status: str = None,
    severity: str = None,
    risk_type: str = None,
    current_user: UserOut = Depends(get_current_user)
) -> List[WorkflowRiskOut]:
    """
    Retrieve workflow risks for the current user.
    """
    return get_workflow_risks_service(
        current_user=current_user,
        project_id=project_id,
        status=status,
        severity=severity,
        risk_type=risk_type
    )
