"""
Pydantic schemas for Projects endpoints.
Aligned with the Supabase public.projects table data model and API requirements.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProjectStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# ---------------------------------------------------------------------------
# Nested helper schemas
# ---------------------------------------------------------------------------

class ProjectManagerOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    is_active: bool = True

    model_config = {
        "from_attributes": True,
    }


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Project name")
    description: Optional[str] = Field(default=None, description="Project description")
    status: ProjectStatus = Field(default=ProjectStatus.PLANNING, description="Project status")
    project_manager_id: Optional[str] = Field(default=None, description="Assigned project manager UUID")
    progress: int = Field(default=0, ge=0, le=100, description="Progress percentage (0-100)")
    start_date: Optional[date] = Field(default=None, description="Project start date")
    end_date: Optional[date] = Field(default=None, description="Project end date")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Project name cannot be empty or whitespace only")
        return trimmed

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectCreateRequest":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "AI DevFlow v2",
                    "description": "Next generation developer workflow suite.",
                    "status": "planning",
                    "project_manager_id": "06c569d4-d002-469f-a3be-79fc1a3c6c5d",
                    "progress": 15,
                    "start_date": "2026-10-01",
                    "end_date": "2027-03-31",
                }
            ]
        }
    }


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255, description="Project name")
    description: Optional[str] = Field(default=None, description="Project description")
    status: Optional[ProjectStatus] = Field(default=None, description="Project status")
    project_manager_id: Optional[str] = Field(default=None, description="Assigned project manager UUID")
    progress: Optional[int] = Field(default=None, ge=0, le=100, description="Progress percentage (0-100)")
    start_date: Optional[date] = Field(default=None, description="Project start date")
    end_date: Optional[date] = Field(default=None, description="Project end date")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            trimmed = v.strip()
            if not trimmed:
                raise ValueError("Project name cannot be empty or whitespace only")
            return trimmed
        return v

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectUpdateRequest":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "AI DevFlow v2 Updated",
                    "status": "active",
                    "progress": 50,
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
    project_manager_id: Optional[str] = None
    progress: int = 0
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    member_count: int = 0
    task_count: int = 0
    project_manager: Optional[ProjectManagerOut] = None

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "examples": [
                {
                    "id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
                    "name": "AI DevFlow v2",
                    "description": "Next generation developer workflow suite.",
                    "status": "active",
                    "project_manager_id": "06c569d4-d002-469f-a3be-79fc1a3c6c5d",
                    "progress": 45,
                    "start_date": "2026-10-01",
                    "end_date": "2027-03-31",
                    "created_at": "2026-09-30T10:00:00Z",
                    "updated_at": "2026-09-30T12:00:00Z",
                    "member_count": 0,
                    "task_count": 0,
                    "project_manager": {
                        "id": "06c569d4-d002-469f-a3be-79fc1a3c6c5d",
                        "name": "Sarah Connor",
                        "email": "sarah@example.com",
                        "role": "project_manager",
                        "is_active": True,
                    },
                }
            ]
        }
    }
