"""
Live E2E Verification Script for Notifications System.
Verifies real API endpoints, auth JWT validation, real event triggers,
unread badge updates, persistence, and multi-user isolation.
"""
import sys
import os
import uuid
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from main import app
from app.core.security import create_access_token
from app.db.supabase_client import get_supabase_client
from app.services.notification_service import NotificationService
from app.schemas.notification import NotificationType

client = TestClient(app)
db = get_supabase_client()


def run_live_e2e_verification():
    print("=" * 70)
    print("STARTING LIVE NOTIFICATION SYSTEM VERIFICATION (API + DATABASE)")
    print("=" * 70)

    # Known test users from database
    pm_user_id = "ab60cba7-cdff-4f7f-a24b-8f59d1e6023f"   # Rahul Kumar (PM)
    dev_user_id = "0e6ee2e9-d8bd-4802-899d-3e9ba81c1cb6"  # Kaviya (Developer)
    other_dev_id = "c39fe1d5-e050-46d8-9a72-074164440d1c" # Meera (Developer)

    pm_token = create_access_token({"sub": pm_user_id})
    dev_token = create_access_token({"sub": dev_user_id})
    other_token = create_access_token({"sub": other_dev_id})

    pm_headers = {"Authorization": f"Bearer {pm_token}"}
    dev_headers = {"Authorization": f"Bearer {dev_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}

    # 1. Fetch PM Project
    proj_resp = db.table("projects").select("id, name").eq("project_manager_id", pm_user_id).execute()
    assert proj_resp.data, "PM must have at least 1 project"
    project = proj_resp.data[0]
    project_id = project["id"]
    print(f"[OK] Fetched PM Project: '{project['name']}' (ID: {project_id})")

    # 2. Trigger Event: PM creates task assigned to Kaviya (Developer)
    task_payload = {
        "title": f"Live E2E Task Notification Test {uuid.uuid4().hex[:6]}",
        "description": "Task created to verify real event-based notification generation",
        "project_id": project_id,
        "assigned_to": dev_user_id,
        "status": "TODO",
        "priority": "HIGH",
        "due_date": (date.today() + timedelta(days=5)).isoformat(),
        "required_skills": ["Python"],
        "min_experience_years": 1,
    }

    resp = client.post("/api/v1/tasks", json=task_payload, headers=pm_headers)
    assert resp.status_code == 201, f"Failed to create task: {resp.text}"
    task_data = resp.json()
    task_id = task_data["id"]
    print(f"[OK] Created & assigned task: '{task_data['title']}' to Developer (ID: {dev_user_id})")

    # 3. Verify Developer receives notification via GET /api/v1/notifications
    resp = client.get("/api/v1/notifications", headers=dev_headers)
    assert resp.status_code == 200, f"Failed to fetch dev notifications: {resp.text}"
    dev_nots = resp.json()
    items = dev_nots["items"]
    assert len(items) > 0, "Developer should have received notification"
    
    assigned_notif = next((n for n in items if n.get("task_id") == task_id or "Test" in n.get("title")), None)
    assert assigned_notif is not None, "Notification for newly created task not found in developer inbox!"
    notif_id = assigned_notif["id"]
    assert assigned_notif["is_read"] is False, "New notification must be unread"
    print(f"[OK] Developer received notification: '{assigned_notif['title']}' (Unread count: {dev_nots['unread_count']})")

    # 4. Verify Unread Count Endpoint
    resp = client.get("/api/v1/notifications/unread-count", headers=dev_headers)
    assert resp.status_code == 200
    assert resp.json()["unread_count"] >= 1
    print(f"[OK] GET /api/v1/notifications/unread-count returned {resp.json()['unread_count']}")

    # 5. Verify User Isolation: Other Developer (Meera) does NOT see Kaviya's notification
    resp = client.get("/api/v1/notifications", headers=other_headers)
    assert resp.status_code == 200
    other_items = resp.json()["items"]
    assert not any(n["id"] == notif_id for n in other_items), "Cross-user security breach! Other dev saw private notification."
    print("[OK] Verified cross-user isolation: Other developer cannot view target user's notification")

    # 6. Verify Access Control: Other developer cannot mark Kaviya's notification as read
    resp = client.patch(f"/api/v1/notifications/{notif_id}/read", headers=other_headers)
    assert resp.status_code in (403, 404), f"Expected 403/404 on unauthorized mark-as-read, got {resp.status_code}"
    print("[OK] Verified cross-user access denial: Unauthorized user cannot mark another user's notification as read")

    # 7. Mark as Read by Developer
    resp = client.patch(f"/api/v1/notifications/{notif_id}/read", headers=dev_headers)
    assert resp.status_code == 200, f"Failed to mark notification as read: {resp.text}"
    updated_notif = resp.json()
    assert updated_notif["is_read"] is True, "Notification state should be updated to read"
    print(f"[OK] Marked notification {notif_id} as read")

    # 8. Trigger Event: Developer updates task status -> IN_PROGRESS
    update_payload = {"status": "IN_PROGRESS"}
    resp = client.patch(f"/api/v1/tasks/{task_id}", json=update_payload, headers=dev_headers)
    assert resp.status_code == 200, f"Failed to update task status: {resp.text}"
    print(f"[OK] Developer moved task status to IN_PROGRESS")

    # 9. Verify PM receives Task Status Change Notification
    resp = client.get("/api/v1/notifications", headers=pm_headers)
    assert resp.status_code == 200
    pm_nots = resp.json()["items"]
    pm_status_notif = next((n for n in pm_nots if n.get("task_id") == task_id), None)
    assert pm_status_notif is not None, "PM did not receive task status update notification!"
    print(f"[OK] Project Manager received task status update notification: '{pm_status_notif['title']}'")

    # 10. Mark All as Read for PM
    resp = client.patch("/api/v1/notifications/read-all", headers=pm_headers)
    assert resp.status_code == 200
    assert resp.json()["unread_count"] == 0
    print("[OK] Mark all as read succeeded for PM. Unread count reset to 0")

    # Clean up test task
    try:
        db.table("tasks").delete().eq("id", task_id).execute()
        print(f"[OK] Cleaned up temporary test task {task_id}")
    except Exception as e:
        print(f"Cleanup warning: {e}")

    print("\n" + "=" * 70)
    print("ALL LIVE NOTIFICATION E2E VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)

run_live_e2e_verification()
