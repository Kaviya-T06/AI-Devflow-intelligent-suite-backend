"""
Pydantic schemas for Activity, Workflow Risks, Repositories, and Settings endpoints.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

class UserSummary(BaseModel):
    id: str
    full_name: str
    email: str

class ActivityOut(BaseModel):
    id: str
    user_id: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    project_id: Optional[str] = None
    description: str
    metadata: Optional[Dict[str, Any]] = None
    created_at: str
    user: Optional[UserSummary] = None


# ---------------------------------------------------------------------------
# Workflow Risks (Re-exported from app.schemas.workflow_risk)
# ---------------------------------------------------------------------------

from app.schemas.workflow_risk import (
    RiskLevel,
    RiskStatus,
    RiskComment,
    RiskHistoryEntry,
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


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

class RepositoryOut(BaseModel):
    """
    Response schema for connected repositories.
    Maps fields from `project_github_repositories` to the frontend Repository type.
    """
    id: str
    project_id: Optional[str] = None
    repository_name: str
    repository_url: str
    provider: str = "GitHub"
    status: str = "Active"
    connected_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsOut(BaseModel):
    notifications_enabled: bool = True
    email_alerts: bool = True
    theme: str = "dark"
    language: str = "en"
    timezone: str = "UTC"
    ai_suggestions_enabled: bool = True
    weekly_report_enabled: bool = True


class SettingsUpdateRequest(BaseModel):
    notifications_enabled: Optional[bool] = None
    email_alerts: Optional[bool] = None
    theme: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    ai_suggestions_enabled: Optional[bool] = None
    weekly_report_enabled: Optional[bool] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "notifications_enabled": True,
                    "theme": "light",
                    "timezone": "Asia/Kolkata",
                }
            ]
        }
    }
