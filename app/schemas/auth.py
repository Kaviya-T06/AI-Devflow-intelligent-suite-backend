"""
Pydantic schemas for Authentication endpoints.

Roles are stored in the `users` table as lowercase strings:
  admin | developer | project_manager
"""
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RoleEnum(str, Enum):
    ADMIN           = "admin"
    DEVELOPER       = "developer"
    PROJECT_MANAGER = "project_manager"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name:     str
    email:    EmailStr
    password: str
    role:     RoleEnum = RoleEnum.DEVELOPER

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
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
                    "name":     "Jane Smith",
                    "email":    "jane@example.com",
                    "password": "secret123",
                    "role":     "developer",
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
