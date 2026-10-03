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
    description: str
    created_at: str
    user: Optional[UserSummary] = None


# ---------------------------------------------------------------------------
# Workflow Risks
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class WorkflowRiskOut(BaseModel):
    id: str
    risk_type: str
    title: str
    description: str
    severity: RiskLevel
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    task_id: Optional[str] = None
    status: str
    is_resolved: bool = False
    detected_at: str
    resolved_at: Optional[str] = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

class RepositoryOut(BaseModel):
    id: str
    name: str
    full_name: str
    description: Optional[str] = None
    url: str
    language: Optional[str] = None
    stars: int = 0
    open_issues: int = 0
    last_pushed_at: Optional[str] = None
    project_id: Optional[str] = None


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
