"""
Pydantic schemas for user profiles.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    DEVELOPER = "DEVELOPER"


# ---------------------------------------------------------------------------
# Profile schemas
# ---------------------------------------------------------------------------

class ProfileBase(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: UserRole = UserRole.DEVELOPER
    avatar_url: Optional[str] = None
    is_active: bool = True
    skills: list[dict] = []
    experience_years: int = 0
    preferred_role: Optional[str] = None
    capacity_hours_per_week: int = 40
    relevant_experience: list[dict] = []


class ProfileCreate(ProfileBase):
    id: UUID
    email: EmailStr
    full_name: str


class ProfileUpdate(BaseModel):
    """Fields a user is allowed to update themselves."""
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    skills: Optional[list[dict]] = None
    experience_years: Optional[int] = None
    preferred_role: Optional[str] = None
    capacity_hours_per_week: Optional[int] = None
    relevant_experience: Optional[list[dict]] = None

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v.strip()) == 0:
            raise ValueError("full_name must not be blank")
        return v


class ProfileRead(ProfileBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Health check schema
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
