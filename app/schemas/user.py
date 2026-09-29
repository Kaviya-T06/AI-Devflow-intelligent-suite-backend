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
    id: str
    name: str
    email: EmailStr
    role: RoleEnum
    is_active: bool

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[RoleEnum] = None
    is_active: Optional[bool] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "Jane Smith Updated",
                    "role": "project_manager",
                    "is_active": True,
                }
            ]
        }
    }
