"""
Pydantic schemas for Workflow Risks — detection, investigation, corrective action, escalation, and resolution.
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class RiskStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


class RiskComment(BaseModel):
    id: str
    user_id: str
    user_name: str
    user_role: Optional[str] = None
    content: str
    is_blocker: bool = False
    evidence_url: Optional[str] = None
    created_at: str


class RiskHistoryEntry(BaseModel):
    id: str
    timestamp: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    action: str
    details: str
    previous_value: Optional[str] = None
    new_value: Optional[str] = None


class WorkflowRiskOut(BaseModel):
    id: str
    risk_type: str
    title: str
    description: str
    severity: RiskLevel
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    task_id: Optional[str] = None
    status: str = "OPEN"
    is_resolved: bool = False
    detected_at: str
    resolved_at: Optional[str] = None
    created_at: str
    updated_at: str

    # Investigation & Entity relationships
    task_title: Optional[str] = None
    task_status: Optional[str] = None
    task_due_date: Optional[str] = None
    overdue_duration_days: Optional[int] = None
    assignee_id: Optional[str] = None
    assignee_name: Optional[str] = None
    project_manager_id: Optional[str] = None
    project_manager_name: Optional[str] = None
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None

    # Detection & Severity explanations
    detection_rule: Optional[str] = None
    detection_reason: Optional[str] = None
    severity_reason: Optional[str] = None

    # Corrective action tracking
    corrective_action_plan: Optional[str] = None
    recovery_deadline: Optional[str] = None
    action_owner_id: Optional[str] = None
    action_owner_name: Optional[str] = None
    action_status: str = "NOT_STARTED"
    is_action_overdue: bool = False

    # Escalations
    is_escalated: bool = False
    escalated_to: Optional[str] = None
    escalated_by: Optional[str] = None
    escalated_by_name: Optional[str] = None
    escalated_at: Optional[str] = None
    escalation_reason: Optional[str] = None

    # Resolution & Verification
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_by_name: Optional[str] = None
    verified_by: Optional[str] = None
    verified_by_name: Optional[str] = None
    verification_notes: Optional[str] = None

    # Recurrence & Links
    is_recurring: bool = False
    recurrence_count: int = 0
    github_pr_url: Optional[str] = None
    submission_id: Optional[str] = None

    # Discussion & Audit
    comments_count: int = 0
    comments: List[RiskComment] = []
    history: List[RiskHistoryEntry] = []


class WorkflowRiskMetricsOut(BaseModel):
    total_risks: int = 0
    open_risks: int = 0
    in_progress_risks: int = 0
    resolved_risks: int = 0
    reopened_risks: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    overdue_tasks_count: int = 0
    review_delays_count: int = 0
    stuck_tasks_count: int = 0
    project_delays_count: int = 0
    workload_risks_count: int = 0
    overdue_actions_count: int = 0
    escalated_count: int = 0
    recurring_count: int = 0


# Request Schemas
class RiskStatusUpdateRequest(BaseModel):
    status: str
    resolution_notes: Optional[str] = None
    reason: Optional[str] = None


class RiskActionPlanRequest(BaseModel):
    action_plan: str
    recovery_deadline: Optional[str] = None
    action_owner_id: Optional[str] = None


class RiskCommentRequest(BaseModel):
    content: str
    is_blocker: bool = False
    evidence_url: Optional[str] = None


class RiskEscalateRequest(BaseModel):
    escalate_to: str = Field(default="PROJECT_MANAGER", description="PROJECT_MANAGER or ADMIN")
    reason: str


class RiskResolveRequest(BaseModel):
    resolution_notes: str
    verify: bool = False
    verification_notes: Optional[str] = None


class RiskReopenRequest(BaseModel):
    reason: str


class RiskAssignRequest(BaseModel):
    owner_id: str
