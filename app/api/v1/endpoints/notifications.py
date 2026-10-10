"""
Notifications Endpoints — authenticated notification operations.
Uses authenticated JWT identity from `get_current_user`.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user
from app.schemas.notification import (
    NotificationListOut,
    NotificationOut,
    UnreadCountOut,
)
from app.schemas.user import UserOut
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListOut)
def list_notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: UserOut = Depends(get_current_user),
):
    """
    List notifications for the authenticated user.
    Only returns notifications belonging to the logged-in recipient.
    """
    items, unread_count, total = NotificationService.list_user_notifications(
        user_id=str(current_user.id), limit=limit, offset=offset
    )
    return NotificationListOut(
        items=items,
        unread_count=unread_count,
        total=total,
    )


@router.get("/unread-count", response_model=UnreadCountOut)
def get_unread_count(
    current_user: UserOut = Depends(get_current_user),
):
    """
    Return the number of unread notifications for the authenticated user.
    """
    count = NotificationService.get_unread_count(str(current_user.id))
    return UnreadCountOut(unread_count=count)


@router.patch("/read-all", response_model=UnreadCountOut)
def mark_all_as_read(
    current_user: UserOut = Depends(get_current_user),
):
    """
    Mark all notifications for the authenticated user as read.
    """
    NotificationService.mark_all_as_read(str(current_user.id))
    unread_count = NotificationService.get_unread_count(str(current_user.id))
    return UnreadCountOut(unread_count=unread_count)


@router.patch("/{notification_id}/read", response_model=NotificationOut)
def mark_notification_as_read(
    notification_id: str,
    current_user: UserOut = Depends(get_current_user),
):
    """
    Mark a single notification as read if owned by the authenticated user.
    """
    return NotificationService.mark_as_read(
        user_id=str(current_user.id), notification_id=notification_id
    )


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_notification(
    notification_id: str,
    current_user: UserOut = Depends(get_current_user),
):
    """
    Delete / dismiss a notification owned by the authenticated user.
    """
    NotificationService.delete_notification(
        user_id=str(current_user.id), notification_id=notification_id
    )
    return None


@router.post("/check-overdue", response_model=dict)
def check_overdue_tasks(
    current_user: UserOut = Depends(get_current_user),
):
    """
    Scan for overdue tasks and generate overdue notifications.
    """
    created_count = NotificationService.check_and_create_overdue_notifications()
    return {"status": "ok", "notifications_created": created_count}
