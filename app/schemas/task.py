"""
Pydantic schemas for Tasks endpoints.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"


class TaskPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    project_id: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[date] = None
    required_skills: list[str] = []
    min_experience_years: int = 0
    estimated_effort: Optional[float] = Field(None, ge=0)
    estimated_hours: Optional[float] = Field(None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def sync_estimates(cls, data):
        if isinstance(data, dict):
            effort = data.get("estimated_hours")
            if effort is None:
                effort = data.get("estimated_effort")
            if effort is not None:
                data["estimated_hours"] = float(effort)
                data["estimated_effort"] = float(effort)
        return data

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Implement developer dashboard",
                    "description": "Create the role-based dashboard for developers.",
                    "status": "TODO",
                    "priority": "HIGH",
                    "project_id": "project-uuid-here",
                    "assigned_to": "user-uuid-here",
                    "due_date": "2026-10-15",
                    "estimated_hours": 16.0,
                }
            ]
        }
    }

class TaskUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    project_id: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[date] = None
    required_skills: Optional[list[str]] = None
    min_experience_years: Optional[int] = None
    estimated_effort: Optional[float] = Field(None, ge=0)
    estimated_hours: Optional[float] = Field(None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def sync_estimates(cls, data):
        if isinstance(data, dict):
            effort = data.get("estimated_hours")
            if effort is None:
                effort = data.get("estimated_effort")
            if effort is not None:
                data["estimated_hours"] = float(effort)
                data["estimated_effort"] = float(effort)
        return data

# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TaskOut(BaseModel):
    id: str
    project_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    status: TaskStatus
    priority: TaskPriority
    due_date: Optional[date] = None
    required_skills: list[str] = []
    min_experience_years: int = 0
    estimated_effort: Optional[float] = None
    estimated_hours: Optional[float] = None
    created_at: Optional[datetime] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    review_started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Joined fields
    project_name: Optional[str] = None
    developer_name: Optional[str] = None

    # Optional joined entities
    project: Optional[dict] = None
    assignee: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def sync_estimates(cls, data):
        if isinstance(data, dict):
            effort = data.get("estimated_hours")
            if effort is None:
                effort = data.get("estimated_effort")
            data["estimated_hours"] = float(effort) if effort is not None else None
            data["estimated_effort"] = float(effort) if effort is not None else None
        return data

    model_config = {
        "from_attributes": True,
    }

