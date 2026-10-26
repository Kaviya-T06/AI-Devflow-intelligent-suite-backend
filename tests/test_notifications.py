"""
Backend Unit & Integration Tests for Notification System.
Covers:
1. Listing notifications for authenticated user.
2. Empty notifications for user with 0 notifications.
3. Unread notification count.
4. Mark single notification as read (preserves notification in history, populates read_at).
5. Filter by status: 'all', 'unread', 'read'.
6. Mark all notifications as read.
7. Cross-user notification access denial (403/404).
8. Delete notification (explicit deletion only).
9. Event-based creation on task assignment.
10. Overdue notification generation logic.
11. Deduplication prevention (prevents duplicates within 60s).
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
    assert n1.read_at is None

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

    # 6. User 1 marks single notification as read:
    # Notification remains in history (NOT deleted) and has read_at set
    marked = NotificationService.mark_as_read(user1_id, n1.id)
    assert marked.is_read is True
    assert marked.read_at is not None
    assert NotificationService.get_unread_count(user1_id) == 0

    # Verify notification is still present in history
    all_items, unread_cnt, total_cnt = NotificationService.list_user_notifications(user1_id, status_filter="all")
    assert len(all_items) == 1
    assert all_items[0].id == n1.id
    assert all_items[0].is_read is True
    assert unread_cnt == 0

    # Filter unread: should return empty
    unread_items, _, _ = NotificationService.list_user_notifications(user1_id, status_filter="unread")
    assert len(unread_items) == 0

    # Filter read: should return n1
    read_items, _, _ = NotificationService.list_user_notifications(user1_id, status_filter="read")
    assert len(read_items) == 1
    assert read_items[0].id == n1.id

    # 7. Create another notification and test mark_all_as_read
    n2 = NotificationService.create_notification(
        recipient_id=user1_id,
        type=NotificationType.RISK_ALERT,
        title="Critical Risk Alert",
        message="A critical workflow risk was detected.",
    )
    assert NotificationService.get_unread_count(user1_id) == 1

    # Now All should have 2 notifications, Unread 1, Read 1
    items_all, unread_cnt, _ = NotificationService.list_user_notifications(user1_id, status_filter="all")
    assert len(items_all) == 2
    assert unread_cnt == 1

    items_unread, _, _ = NotificationService.list_user_notifications(user1_id, status_filter="unread")
    assert len(items_unread) == 1
    assert items_unread[0].id == n2.id

    updated_count = NotificationService.mark_all_as_read(user1_id)
    assert updated_count >= 1
    assert NotificationService.get_unread_count(user1_id) == 0

    # Both are now read and remain in history
    items_after, unread_after, _ = NotificationService.list_user_notifications(user1_id, status_filter="all")
    assert len(items_after) == 2
    assert unread_after == 0

    # 8. User 2 cannot delete User 1's notification
    with pytest.raises(Exception) as excinfo:
        NotificationService.delete_notification(user2_id, n2.id)
    assert "403" in str(excinfo.value) or "Forbidden" in str(excinfo.value) or "404" in str(excinfo.value)

    # 9. User 1 deletes notification (explicit user action)
    deleted = NotificationService.delete_notification(user1_id, n2.id)
    assert deleted is True

    # After explicit deletion, only n1 remains
    items_remaining, _, _ = NotificationService.list_user_notifications(user1_id, status_filter="all")
    assert len(items_remaining) == 1
    assert items_remaining[0].id == n1.id

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

    # GET /api/v1/notifications with status filter
    res_unread = client.get("/api/v1/notifications?status=unread", headers=headers)
    assert res_unread.status_code == 200
    res_read = client.get("/api/v1/notifications?status=read", headers=headers)
    assert res_read.status_code == 200

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


def test_overdue_task_notification_enrichment_and_project_relationship():
    """
    Verify:
    1. Overdue task notifications show actual project name in title: 'Overdue Task — {project_name}'.
    2. Overdue task notifications display 'Overdue Task — Project name unavailable' when project name is unavailable.
    3. Overdue task notifications enrich task title, developer name, due date, and days overdue.
    """
    user_id = str(uuid.uuid4())
    proj_id = "9706106d-c1e5-4e6d-afeb-b5da43abfd9e"  # AI Customer Support Platform
    task_id = "6297f50a-6114-4cea-bf39-89eb70021229"

    # Notification with project_id and task_id
    n = NotificationService.create_notification(
        recipient_id=user_id,
        type=NotificationType.TASK_OVERDUE,
        title="Overdue Task in Project",
        message="Task 'Create database schema and integration' was due on 2026-09-29 and is currently overdue.",
        project_id=proj_id,
        task_id=task_id,
    )

    # When retrieved via list_user_notifications, it should be enriched
    items, _, _ = NotificationService.list_user_notifications(user_id)
    assert len(items) >= 1
    overdue_notif = next((i for i in items if i.id == n.id), None)
    assert overdue_notif is not None
    assert overdue_notif.title == "Overdue Task — AI Customer Support Platform"
    assert overdue_notif.project_name == "AI Customer Support Platform"
    assert overdue_notif.task_title == "Create database schema and integration"
    assert overdue_notif.days_overdue is not None
    assert overdue_notif.days_overdue > 0

    # Notification without project or with unavailable project
    n_unavail = NotificationService.create_notification(
        recipient_id=user_id,
        type=NotificationType.TASK_OVERDUE,
        title="Overdue Task in Project",
        message="Generic overdue task message.",
        project_id=None,
        task_id=None,
    )

    items, _, _ = NotificationService.list_user_notifications(user_id)
    unavail_notif = next((i for i in items if i.id == n_unavail.id), None)
    assert unavail_notif is not None
    assert unavail_notif.title == "Overdue Task — Project name unavailable"
    assert unavail_notif.project_name is None
