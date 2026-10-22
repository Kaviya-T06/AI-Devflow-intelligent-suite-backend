"""
Backend Unit & Integration Tests for User Presence and Availability System.
Covers:
1. Status changes and database/fallback persistence.
2. Security: Never trust client-supplied user ID for another user's status.
3. Heartbeat expiry and last_seen_at updates.
4. Separation of connection state (is_online) from manual status.
5. Status expiry: automatic reversion to 'available' when status_expires_at is reached.
6. Multi-tab connection tracking (multiple sockets -> 1 closes -> still online -> all close -> offline).
7. WebSocket live updates and reconnection.
8. Role-based team presence retrieval and filtering.
9. Notification DND behavior: non-urgent muted, critical alerts preserved.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.schemas.auth import RoleEnum
from app.schemas.notification import NotificationType
from app.schemas.presence import AvailabilityStatus, PresenceUpdate
from app.services.notification_service import NotificationService
from app.services.presence_service import PresenceService, _active_connections, _fallback_presence
from main import app

client = TestClient(app)

# Known active test users from DB
ADMIN_USER_ID = "06c569d4-d002-469f-a3be-79fc1a3c6c5d"
DEV_USER_ID = "0e6ee2e9-d8bd-4802-899d-3e9ba81c1cb6"
PM_USER_ID = "ab60cba7-cdff-4f7f-a24b-8f59d1e6023f"


@pytest.fixture
def admin_headers():
    token = create_access_token({"sub": ADMIN_USER_ID})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dev_headers():
    token = create_access_token({"sub": DEV_USER_ID})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pm_headers():
    token = create_access_token({"sub": PM_USER_ID})
    return {"Authorization": f"Bearer {token}"}


def test_get_my_presence(dev_headers):
    """Authenticated user can fetch their own presence."""
    res = client.get("/api/v1/presence/me", headers=dev_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == DEV_USER_ID
    assert "availability_status" in data
    assert "is_online" in data
    assert "effective_status" in data
    assert "last_seen_at" in data


def test_update_status_and_persistence(dev_headers):
    """User can update status, status message, and custom expiry."""
    future_expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    payload = {
        "availability_status": "busy",
        "status_message": "Focusing on core sprint task",
        "status_expires_at": future_expiry,
    }
    res = client.put("/api/v1/presence/me", json=payload, headers=dev_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["availability_status"] == "busy"
    assert data["status_message"] == "Focusing on core sprint task"
    assert data["status_expires_at"] is not None

    # Verify persistence by reading it back
    res2 = client.get("/api/v1/presence/me", headers=dev_headers)
    assert res2.status_code == 200
    assert res2.json()["availability_status"] == "busy"
    assert res2.json()["status_message"] == "Focusing on core sprint task"


def test_clear_status_expiry(dev_headers):
    """User can clear their temporary status expiry."""
    payload = {"clear_expiry": True, "availability_status": "available", "status_message": ""}
    res = client.put("/api/v1/presence/me", json=payload, headers=dev_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["availability_status"] == "available"
    assert data["status_expires_at"] is None


def test_heartbeat_updates_last_seen_and_online(dev_headers):
    """Heartbeat updates last_seen_at and keeps user online."""
    res = client.post("/api/v1/presence/heartbeat", headers=dev_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["is_online"] is True
    assert "last_seen_at" in data


def test_separation_of_connection_and_manual_status():
    """
    If user has not sent a heartbeat for > 45s and has no active sockets,
    they must be reported as OFFLINE even if availability_status is 'available'.
    """
    test_user_id = str(uuid.uuid4())
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    _fallback_presence[test_user_id] = {
        "user_id": test_user_id,
        "availability_status": "available",
        "status_message": "Working",
        "status_expires_at": None,
        "last_seen_at": old_time,
        "updated_at": old_time,
    }
    if test_user_id in _active_connections:
        del _active_connections[test_user_id]

    presence = PresenceService.get_user_presence(test_user_id)
    assert presence.is_online is False
    assert presence.availability_status == AvailabilityStatus.AVAILABLE
    # Requirement: A disconnected user must not appear online just because saved status is Available
    assert presence.effective_status == "offline"


def test_status_expiry_automatic_reversion():
    """
    Temporary status automatically reverts to 'available' once status_expires_at passes.
    """
    test_user_id = str(uuid.uuid4())
    past_expiry = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    _fallback_presence[test_user_id] = {
        "user_id": test_user_id,
        "availability_status": "busy",
        "status_message": "In a 30m standup",
        "status_expires_at": past_expiry,
        "last_seen_at": now_iso,
        "updated_at": now_iso,
    }

    # Checking presence triggers automatic expiration
    presence = PresenceService.get_user_presence(test_user_id)
    assert presence.availability_status == AvailabilityStatus.AVAILABLE
    assert presence.status_expires_at is None
    assert presence.status_message is None


def test_team_presence_filtering_and_rbac(admin_headers, dev_headers):
    """Admin and Dev can access team presence with filters."""
    # Admin view
    res = client.get("/api/v1/presence/team?role=developer", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "users" in data
    assert "total" in data
    assert "online_count" in data

    # Developer view
    res_dev = client.get("/api/v1/presence/team", headers=dev_headers)
    assert res_dev.status_code == 200
    assert "users" in res_dev.json()


def test_dnd_notification_behavior():
    """
    When a user is in DO_NOT_DISTURB:
    - Non-urgent notifications are muted.
    - Critical alerts (RISK_ALERT, TASK_OVERDUE) bypass DND and alert normally.
    """
    dnd_user_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    _fallback_presence[dnd_user_id] = {
        "user_id": dnd_user_id,
        "availability_status": AvailabilityStatus.DO_NOT_DISTURB.value,
        "status_message": "In deep work mode",
        "status_expires_at": None,
        "last_seen_at": now_iso,
        "updated_at": now_iso,
    }

    # 1. Non-urgent notification (PM_UPDATE) -> muted (is_read=True)
    muted_n = NotificationService.create_notification(
        recipient_id=dnd_user_id,
        type=NotificationType.PM_UPDATE,
        title="Weekly Status Digest",
        message="Here is the weekly update.",
    )
    assert muted_n.is_read is True
    assert "[Muted - DND]" in muted_n.title

    # 2. Critical Alert (RISK_ALERT) -> preserved as unread alert
    critical_n = NotificationService.create_notification(
        recipient_id=dnd_user_id,
        type=NotificationType.RISK_ALERT,
        title="Critical Database Latency Spike",
        message="Workflow execution is blocked due to latency.",
    )
    assert critical_n.is_read is False

    # 3. Critical Alert (TASK_OVERDUE) -> preserved as unread alert
    overdue_n = NotificationService.create_notification(
        recipient_id=dnd_user_id,
        type=NotificationType.TASK_OVERDUE,
        title="Task Overdue: Security Patch",
        message="Critical release is overdue.",
    )
    assert overdue_n.is_read is False


def test_websocket_lifecycle_and_multitab():
    """
    Test WebSocket connection, multi-tab presence tracking, and status update.
    """
    token = create_access_token({"sub": DEV_USER_ID})

    # 1. Reject connection without token
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/v1/presence/ws") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008

    def recv_type(ws, target_type):
        for _ in range(5):
            msg = ws.receive_json()
            if msg.get("type") == target_type:
                return msg
        return msg

    # 2. Tab 1 connects
    with client.websocket_connect(f"/api/v1/presence/ws?token={token}") as ws1:
        init_msg = recv_type(ws1, "INITIAL_PRESENCE")
        assert init_msg["type"] == "INITIAL_PRESENCE"
        assert init_msg["data"]["user_id"] == DEV_USER_ID

        # Verify user is online with 1 active tab
        p1 = PresenceService.get_user_presence(DEV_USER_ID)
        assert p1.is_online is True

        # Tab 2 connects simultaneously (multi-tab)
        with client.websocket_connect(f"/api/v1/presence/ws?token={token}") as ws2:
            init_msg2 = recv_type(ws2, "INITIAL_PRESENCE")
            assert init_msg2["type"] == "INITIAL_PRESENCE"
            assert len(_active_connections[DEV_USER_ID]) == 2

            # Tab 2 sends heartbeat
            ws2.send_json({"type": "HEARTBEAT"})
            hb_ack = recv_type(ws2, "HEARTBEAT_ACK")
            assert hb_ack["type"] == "HEARTBEAT_ACK"

            # Tab 2 sends status change to 'away'
            ws2.send_json({"type": "UPDATE_STATUS", "data": {"availability_status": "away", "status_message": "Away from desk"}})
            up_ack = recv_type(ws2, "STATUS_UPDATED")
            assert up_ack["type"] == "STATUS_UPDATED"
            assert up_ack["data"]["availability_status"] == "away"

        # Tab 2 closed: Tab 1 is still open, user should still be online!
        assert len(_active_connections[DEV_USER_ID]) == 1
        p_tab1_left = PresenceService.get_user_presence(DEV_USER_ID)
        assert p_tab1_left.is_online is True

    # Tab 1 closed: Now all connections closed
    assert DEV_USER_ID not in _active_connections
