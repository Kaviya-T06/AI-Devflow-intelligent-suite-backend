"""
Workflow Risks router — production-quality endpoints for detection, investigation,
corrective action planning, comments/evidence, escalation, and lifecycle transitions.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status

from app.schemas.workflow_risk import (
    WorkflowRiskOut,
    WorkflowRiskMetricsOut,
    RiskStatusUpdateRequest,
    RiskActionPlanRequest,
    RiskCommentRequest,
    RiskEscalateRequest,
    RiskResolveRequest,
    RiskReopenRequest,
    RiskAssignRequest,
)
from app.schemas.user import UserOut
from app.api.deps import get_current_user
from app.services.workflow_risk_service import (
    get_workflow_risks_service,
    get_workflow_risk_detail_service,
    update_risk_status_service,
    set_corrective_action_plan_service,
    add_risk_comment_service,
    escalate_risk_service,
    resolve_risk_service,
    reopen_risk_service,
    assign_risk_owner_service,
    get_workflow_risk_metrics_service,
)

router = APIRouter(prefix="/workflow-risks", tags=["Workflow Risks"])


@router.get("", response_model=List[WorkflowRiskOut], summary="Get detected workflow risks")
async def get_workflow_risks(
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(None, description="Filter by status (OPEN, IN_PROGRESS, RESOLVED, REOPENED)"),
    severity: Optional[str] = Query(None, description="Filter by severity (Low, Medium, High, Critical)"),
    risk_type: Optional[str] = Query(None, description="Filter by risk type"),
    owner_id: Optional[str] = Query(None, description="Filter by owner user ID"),
    is_escalated: Optional[bool] = Query(None, description="Filter by escalated flag"),
    search: Optional[str] = Query(None, description="Search term for title, description, project, or task"),
    limit: Optional[int] = Query(None, description="Max records to return"),
    offset: Optional[int] = Query(None, description="Offset for pagination"),
    current_user: UserOut = Depends(get_current_user),
) -> List[WorkflowRiskOut]:
    """Retrieve workflow risks for the current user scoped by role permissions."""
    return get_workflow_risks_service(
        current_user=current_user,
        project_id=project_id,
        status=status,
        severity=severity,
        risk_type=risk_type,
        owner_id=owner_id,
        is_escalated=is_escalated,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/metrics/summary", response_model=WorkflowRiskMetricsOut, summary="Get summary risk metrics")
async def get_workflow_risk_metrics(
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskMetricsOut:
    """Retrieve accurate live aggregated metrics for workflow risks dashboard."""
    return get_workflow_risk_metrics_service(current_user=current_user)


@router.get("/{risk_id}", response_model=WorkflowRiskOut, summary="Get full risk details")
async def get_workflow_risk_detail(
    risk_id: str,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Retrieve complete investigation details, action plan, evidence, and audit timeline for a risk."""
    return get_workflow_risk_detail_service(risk_id=risk_id, current_user=current_user)


@router.patch("/{risk_id}", response_model=WorkflowRiskOut, summary="Update risk status / lifecycle")
async def update_risk_status(
    risk_id: str,
    payload: RiskStatusUpdateRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Transition risk lifecycle (OPEN, IN_PROGRESS, RESOLVED, REOPENED) with validation."""
    return update_risk_status_service(
        risk_id=risk_id,
        new_status=payload.status,
        current_user=current_user,
        resolution_notes=payload.resolution_notes,
        reason=payload.reason,
    )


@router.post("/{risk_id}/action", response_model=WorkflowRiskOut, summary="Set corrective-action plan")
async def set_corrective_action_plan(
    risk_id: str,
    payload: RiskActionPlanRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Record or update a corrective action plan, recovery deadline, and action owner."""
    return set_corrective_action_plan_service(
        risk_id=risk_id,
        action_plan=payload.action_plan,
        recovery_deadline=payload.recovery_deadline,
        action_owner_id=payload.action_owner_id,
        current_user=current_user,
    )


@router.post("/{risk_id}/comments", response_model=WorkflowRiskOut, summary="Add comment, blocker, or evidence")
async def add_risk_comment(
    risk_id: str,
    payload: RiskCommentRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Add a progress update, discussion comment, blocker alert, or deliverable/PR evidence link."""
    return add_risk_comment_service(
        risk_id=risk_id,
        content=payload.content,
        is_blocker=payload.is_blocker,
        evidence_url=payload.evidence_url,
        current_user=current_user,
    )


@router.post("/{risk_id}/escalate", response_model=WorkflowRiskOut, summary="Escalate risk to PM or Admin")
async def escalate_risk(
    risk_id: str,
    payload: RiskEscalateRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Escalate a workflow risk to the relevant Project Manager or Admin with mandatory reason."""
    return escalate_risk_service(
        risk_id=risk_id,
        escalate_to=payload.escalate_to,
        reason=payload.reason,
        current_user=current_user,
    )


@router.post("/{risk_id}/resolve", response_model=WorkflowRiskOut, summary="Resolve risk with verification")
async def resolve_risk(
    risk_id: str,
    payload: RiskResolveRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Explicitly resolve a workflow risk with resolution notes and optional formal verification sign-off."""
    return resolve_risk_service(
        risk_id=risk_id,
        resolution_notes=payload.resolution_notes,
        verify=payload.verify,
        verification_notes=payload.verification_notes,
        current_user=current_user,
    )


@router.post("/{risk_id}/reopen", response_model=WorkflowRiskOut, summary="Reopen resolved risk")
async def reopen_risk(
    risk_id: str,
    payload: RiskReopenRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Reopen a previously resolved workflow risk when condition returns or further action is required."""
    return reopen_risk_service(
        risk_id=risk_id,
        reason=payload.reason,
        current_user=current_user,
    )


@router.post("/{risk_id}/assign", response_model=WorkflowRiskOut, summary="Assign risk owner")
async def assign_risk_owner(
    risk_id: str,
    payload: RiskAssignRequest,
    current_user: UserOut = Depends(get_current_user),
) -> WorkflowRiskOut:
    """Assign or reassign ownership of a workflow risk to an authorized user."""
    return assign_risk_owner_service(
        risk_id=risk_id,
        owner_id=payload.owner_id,
        current_user=current_user,
    )
