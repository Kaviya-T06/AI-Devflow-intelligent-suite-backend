"""
Pydantic schemas for Tasks endpoints.
"""
from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    IN_REVIEW = "In Review"
    DONE = "Done"
    BLOCKED = "Blocked"


class TaskPriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    project_id: Optional[str] = None
    assignee_id: Optional[str] = None
    due_date: Optional[date] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Implement user authentication",
                    "description": "Set up JWT-based auth flow.",
                    "status": "Todo",
                    "priority": "High",
                    "project_id": "project-uuid-here",
                    "assignee_id": "user-uuid-here",
                    "due_date": "2026-10-15",
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TaskOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    status: TaskStatus
    priority: TaskPriority
    project_id: Optional[str] = None
    assignee_id: Optional[str] = None
    due_date: Optional[date] = None
