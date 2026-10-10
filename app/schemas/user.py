"""
Pydantic schemas for Users endpoints.
"""
from typing import Optional
from pydantic import BaseModel, EmailStr

from app.schemas.auth import RoleEnum


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class UserOut(BaseModel):
    id:         str
    name:       str
    email:      EmailStr
    role:       RoleEnum
    is_active:  bool
    created_at: Optional[str] = None
    
    # Developer profiling fields
    skills: Optional[list] = None
    experience_years: Optional[int] = None
    capacity_hours_per_week: Optional[int] = None
    preferred_role: Optional[str] = None
    relevant_experience: Optional[list] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class UserCreateRequest(BaseModel):
    name:      str
    email:     EmailStr
    password:  str
    role:      RoleEnum = RoleEnum.DEVELOPER
    is_active: bool = True

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name":      "Jane Smith",
                    "email":     "jane@example.com",
                    "password":  "secret123",
                    "role":      "developer",
                    "is_active": True,
                }
            ]
        }
    }


class UserUpdateRequest(BaseModel):
    name:      Optional[str]      = None
    email:     Optional[EmailStr] = None
    role:      Optional[RoleEnum] = None
    is_active: Optional[bool]     = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name":      "Jane Smith Updated",
                    "role":      "project_manager",
                    "is_active": True,
                }
            ]
        }
    }


class UserStatusRequest(BaseModel):
    is_active: bool


class UserSelfUpdateRequest(BaseModel):
    name: Optional[str] = None
    full_name: Optional[str] = None  # alias accepted for compatibility
    skills: Optional[list] = None
    experience_years: Optional[int] = None
    capacity_hours_per_week: Optional[int] = None
    preferred_role: Optional[str] = None
    relevant_experience: Optional[list] = None
