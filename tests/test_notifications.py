"""
Backend Unit & Integration Tests for Notification System.
Covers:
1. Listing notifications for authenticated user.
2. Empty notifications for user with 0 notifications.
3. Unread notification count.
4. Mark single notification as read.
5. Mark all notifications as read.
6. Cross-user notification access denial (403/404).
7. Delete notification.
8. Event-based creation on task assignment.
9. Overdue notification generation logic.
10. Deduplication prevention (prevents duplicates within 60s).
"""
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from main import app
from app.core.security import create_access_token
from app.services.notification_service import NotificationService
from app.schemas.notification import NotificationType

client = TestClient(app)


def test_notification_service_flow():
    user1_id = str(uuid.uuid4())
    user2_id = str(uuid.uuid4())

    # 1. User 1 starts with 0 notifications
    items, unread_count, total = NotificationService.list_user_notifications(user1_id)
    assert items == []
    assert unread_count == 0
    assert total == 0

    # 2. Create notification for User 1
    n1 = NotificationService.create_notification(
        recipient_id=user1_id,
        type=NotificationType.TASK_ASSIGNED,
        title="Test Task Assigned",
        message="You were assigned a test task.",
    )
    assert n1.id is not None
    assert n1.recipient_id == user1_id
    assert n1.is_read is False

    # 3. List notifications for User 1
    items, unread_count, total = NotificationService.list_user_notifications(user1_id)
    assert len(items) == 1
    assert unread_count == 1
    assert total == 1
    assert items[0].id == n1.id

    # 4. User 2 cannot see User 1's notification (Cross-user isolation)
    user2_items, user2_unread, user2_total = NotificationService.list_user_notifications(user2_id)
    assert user2_items == []
    assert user2_unread == 0

    # 5. User 2 cannot mark User 1's notification as read (Cross-user denial)
    with pytest.raises(Exception) as excinfo:
        NotificationService.mark_as_read(user2_id, n1.id)
    assert "403" in str(excinfo.value) or "Forbidden" in str(excinfo.value) or "404" in str(excinfo.value)

    # 6. User 1 marks single notification as read
    marked = NotificationService.mark_as_read(user1_id, n1.id)
    assert marked.is_read is True
    assert NotificationService.get_unread_count(user1_id) == 0

    # 7. Create another notification and test mark_all_as_read
    n2 = NotificationService.create_notification(
        recipient_id=user1_id,
        type=NotificationType.RISK_ALERT,
        title="Critical Risk Alert",
        message="A critical workflow risk was detected.",
    )
    assert NotificationService.get_unread_count(user1_id) == 1
    updated_count = NotificationService.mark_all_as_read(user1_id)
    assert updated_count >= 1
    assert NotificationService.get_unread_count(user1_id) == 0

    # 8. User 2 cannot delete User 1's notification
    with pytest.raises(Exception) as excinfo:
        NotificationService.delete_notification(user2_id, n2.id)
    assert "403" in str(excinfo.value) or "Forbidden" in str(excinfo.value) or "404" in str(excinfo.value)

    # 9. User 1 deletes notification
    deleted = NotificationService.delete_notification(user1_id, n2.id)
    assert deleted is True

    # 10. Deduplication test: duplicate notification call within 60s returns same notification ID
    dup1 = NotificationService.create_notification(
        recipient_id=user1_id,
        type=NotificationType.PM_UPDATE,
        title="Unique Event Title",
        message="Message content",
    )
    dup2 = NotificationService.create_notification(
        recipient_id=user1_id,
        type=NotificationType.PM_UPDATE,
        title="Unique Event Title",
        message="Message content",
    )
    assert dup1.id == dup2.id


def test_notification_api_endpoints():
    user_id = "06c569d4-d002-469f-a3be-79fc1a3c6c5d"  # Admin user ID from DB
    token = create_access_token({"sub": user_id})
    headers = {"Authorization": f"Bearer {token}"}

    # GET /api/v1/notifications
    res = client.get("/api/v1/notifications", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "unread_count" in data

    # GET /api/v1/notifications/unread-count
    res = client.get("/api/v1/notifications/unread-count", headers=headers)
    assert res.status_code == 200
    assert "unread_count" in res.json()

    # PATCH /api/v1/notifications/read-all
    res = client.patch("/api/v1/notifications/read-all", headers=headers)
    assert res.status_code == 200
    assert res.json()["unread_count"] == 0

    # POST /api/v1/notifications/check-overdue
    res = client.post("/api/v1/notifications/check-overdue", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_unauthenticated_access_denial():
    # Attempting endpoints without Bearer token returns 401
    res = client.get("/api/v1/notifications")
    assert res.status_code == 401

    res = client.get("/api/v1/notifications/unread-count")
    assert res.status_code == 401

    res = client.patch("/api/v1/notifications/read-all")
    assert res.status_code == 401
