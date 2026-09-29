"""
Pydantic schemas for Activity, Workflow Risks, Repositories, and Settings endpoints.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

class ActivityOut(BaseModel):
    id: str
    actor_id: str
    actor_name: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    timestamp: str


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
    title: str
    description: str
    level: RiskLevel
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    detected_at: str
    is_resolved: bool = False


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
