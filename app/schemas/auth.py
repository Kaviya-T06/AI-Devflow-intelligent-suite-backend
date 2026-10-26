"""
Pydantic schemas for Authentication endpoints.

Roles are stored in the `users` table as lowercase strings:
  admin | developer | project_manager
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RoleEnum(str, Enum):
    ADMIN           = "admin"
    DEVELOPER       = "developer"
    PROJECT_MANAGER = "project_manager"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            clean = value.lower().strip().replace("-", "_").replace(" ", "_")
            if clean in ("admin",):
                return cls.ADMIN
            if clean in ("developer", "dev", "team_member"):
                return cls.DEVELOPER
            if clean in ("project_manager", "manager", "pm"):
                return cls.PROJECT_MANAGER
        return None


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name:        Optional[str] = None
    full_name:   Optional[str] = None
    email:       EmailStr
    password:    str
    role:        RoleEnum = RoleEnum.DEVELOPER
    invite_code: Optional[str] = None
    inviteCode:  Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data):
        if isinstance(data, dict):
            # Resolve name from full_name or fullName
            if not data.get("name"):
                data["name"] = data.get("full_name") or data.get("fullName")
            # Resolve invite_code from inviteCode
            if not data.get("invite_code") and data.get("inviteCode"):
                data["invite_code"] = data.get("inviteCode")
            # Normalize role if passed as string
            raw_role = data.get("role")
            if raw_role and isinstance(raw_role, str):
                try:
                    data["role"] = RoleEnum(raw_role)
                except Exception:
                    pass
        return data

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("password must be at least 6 characters")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name":        "Jane Smith",
                    "email":       "jane@example.com",
                    "password":    "secret123",
                    "role":        "developer",
                    "invite_code": "DEVFLOW-PM-SECURE-KEY",
                }
            ]
        }
    }


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "email":    "jane@example.com",
                    "password": "secret123",
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AuthUserOut(BaseModel):
    id:    str
    name:  str
    email: EmailStr
    role:  RoleEnum


class RegisterResponse(BaseModel):
    message: str
    user:    AuthUserOut


class LoginResponse(BaseModel):
    message:      str
    access_token: str
    token_type:   str
    user:         AuthUserOut


class LogoutResponse(BaseModel):
    message: str
