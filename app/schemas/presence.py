"""
Pydantic schemas for User Presence and Availability.
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    DO_NOT_DISTURB = "do_not_disturb"
    AWAY = "away"
    OFFLINE = "offline"


class PresenceUpdate(BaseModel):
    availability_status: Optional[AvailabilityStatus] = Field(
        None, description="User manual availability status"
    )
    status_message: Optional[str] = Field(
        None, max_length=200, description="Optional custom status message"
    )
    status_expires_at: Optional[str] = Field(
        None, description="ISO timestamp when temporary status expires"
    )
    clear_expiry: Optional[bool] = Field(
        False, description="Whether to clear the current expiry time"
    )


class PresenceOut(BaseModel):
    user_id: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    availability_status: AvailabilityStatus
    status_message: Optional[str] = None
    status_expires_at: Optional[str] = None
    last_seen_at: str
    is_online: bool
    effective_status: str
    updated_at: str


class HeartbeatOut(BaseModel):
    status: str = "ok"
    last_seen_at: str
    is_online: bool
    availability_status: AvailabilityStatus
    effective_status: str
    status_expires_at: Optional[str] = None


class TeamPresenceListOut(BaseModel):
    users: List[PresenceOut]
    total: int
    online_count: int
    available_count: int
    busy_count: int
