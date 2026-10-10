"""
Pydantic schemas for Notifications endpoints.
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class NotificationType(str, Enum):
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_STATUS_CHANGED = "TASK_STATUS_CHANGED"
    TASK_OVERDUE = "TASK_OVERDUE"
    RISK_ALERT = "RISK_ALERT"
    SMART_ALLOCATION = "SMART_ALLOCATION"
    GITHUB_EVENT = "GITHUB_EVENT"
    PM_UPDATE = "PM_UPDATE"


class NotificationCreate(BaseModel):
    recipient_id: str = Field(..., description="Target user ID")
    type: NotificationType = Field(..., description="Event type")
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    project_id: Optional[str] = Field(None, description="Optional associated project ID")
    task_id: Optional[str] = Field(None, description="Optional associated task ID")


class NotificationOut(BaseModel):
    id: str
    recipient_id: str
    type: NotificationType
    title: str
    message: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    is_read: bool = False
    created_at: str


class UnreadCountOut(BaseModel):
    unread_count: int


class NotificationListOut(BaseModel):
    items: List[NotificationOut]
    unread_count: int
    total: int
