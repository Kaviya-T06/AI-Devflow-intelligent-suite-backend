"""
Pydantic schemas for Projects endpoints.
"""
from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProjectStatus(str, Enum):
    PLANNING = "Planning"
    ACTIVE = "Active"
    ON_HOLD = "On Hold"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    status: ProjectStatus = ProjectStatus.PLANNING
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    owner_id: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "AI DevFlow v2",
                    "description": "Next generation developer workflow suite.",
                    "status": "Planning",
                    "start_date": "2026-10-01",
                    "end_date": "2027-03-31",
                    "owner_id": "user-uuid-here",
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: ProjectStatus
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    owner_id: Optional[str] = None
    member_count: int = 0
    task_count: int = 0
