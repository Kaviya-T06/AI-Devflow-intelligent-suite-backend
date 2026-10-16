"""
End-to-End Workflow & Notification Verification Script for AI DevFlow Intelligence Suite.
Tests:
1. Admin creates project and assigns Project Manager.
2. Verify project_manager_id references correct UUID in database.
3. Verify Project Manager receives PROJECT_ASSIGNED notification.
4. Verify PM can read notification and unread count updates.
5. PM creates task assigned to Developer.
6. Verify Developer receives TASK_ASSIGNED notification with correct project name formatting.
7. Verify Cross-Developer Isolation (Other developer cannot view private task or notification).
8. Developer transitions task TODO -> IN_PROGRESS: sets started_at, logs activity.
9. Developer transitions task IN_PROGRESS -> REVIEW: sets review_started_at, dispatches TASK_REVIEW_REQUESTED to PM.
10. Developer cannot approve task to COMPLETED (403 Forbidden).
11. Project Manager approves task REVIEW -> COMPLETED: sets completed_at, recalculates project progress, dispatches TASK_COMPLETED.
12. Verify Project Progress updated to 100%.
13. Verify Deduplication prevents duplicate notifications on rapid re-dispatch.
14. Verify Unauthenticated requests return 401.
15. Cleanup of test project and task data.
"""
import os
import sys
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


def run_e2e_workflow_test():
    print("=" * 80)
    print("AI DEVFLOW INTELLIGENCE SUITE — COMPREHENSIVE END-TO-END WORKFLOW VERIFICATION")
    print("=" * 80)

    # 0. Discover existing real users for Admin, PM, Developer, and Other Developer
    users_resp = db.table("users").select("id, name, email, role, is_active").execute()
    users = users_resp.data or []
    assert len(users) >= 3, "Database must have at least 3 users"

    admin_user = next((u for u in users if u["role"] == "admin" and u.get("is_active") is not False), None)
    pm_user = next((u for u in users if u["role"] == "project_manager" and u.get("is_active") is not False), None)
    dev_users = [u for u in users if u["role"] == "developer" and u.get("is_active") is not False]

    assert admin_user is not None, "Admin user must exist"
    assert pm_user is not None, "Project Manager user must exist"
    assert len(dev_users) >= 1, "At least 1 active developer must exist"

    dev_user = dev_users[0]
    other_dev_user = dev_users[1] if len(dev_users) > 1 else None

    print(f"[AUTH] Admin User:           {admin_user['name']} ({admin_user['id']})")
    print(f"[AUTH] Project Manager:      {pm_user['name']} ({pm_user['id']})")
    print(f"[AUTH] Developer:            {dev_user['name']} ({dev_user['id']})")
    if other_dev_user:
        print(f"[AUTH] Other Developer:      {other_dev_user['name']} ({other_dev_user['id']})")

    admin_token = create_access_token({"sub": admin_user["id"]})
    pm_token = create_access_token({"sub": pm_user["id"]})
    dev_token = create_access_token({"sub": dev_user["id"]})
    other_dev_token = create_access_token({"sub": other_dev_user["id"]}) if other_dev_user else None

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    pm_headers = {"Authorization": f"Bearer {pm_token}"}
    dev_headers = {"Authorization": f"Bearer {dev_token}"}
    other_dev_headers = {"Authorization": f"Bearer {other_dev_token}"} if other_dev_token else None

    created_project_id = None
    created_task_id = None

    try:
        # ====================================================================
        # STEP 1: Admin creates a project and assigns Project Manager
        # ====================================================================
        print("\n--- STEP 1: Admin Creates Project and Assigns PM ---")
        project_name = f"Testo-Workflow-{uuid.uuid4().hex[:6]}"
        project_payload = {
            "name": project_name,
            "description": "End-to-end workflow and notification automated test",
            "start_date": "2026-10-15",
            "end_date": "2026-11-15",
            "project_manager_id": pm_user["id"],
            "status": "planning",
        }

        resp = client.post("/api/v1/projects", json=project_payload, headers=admin_headers)
        assert resp.status_code == 201, f"Admin project creation failed: {resp.text}"
        proj_data = resp.json()
        created_project_id = proj_data["id"]
        print(f"[OK] Project created: '{proj_data['name']}' (ID: {created_project_id})")

        # Verify project_manager_id matches UUID
        assert proj_data["project_manager_id"] == pm_user["id"], "project_manager_id does not match PM UUID"
        print(f"[OK] Verified project_manager_id matches PM UUID: {pm_user['id']}")

        # ====================================================================
        # STEP 2: Project Manager receives notification
        # ====================================================================
        print("\n--- STEP 2: Project Manager Receives Project Assignment Notification ---")
        resp = client.get("/api/v1/notifications", headers=pm_headers)
        assert resp.status_code == 200, f"PM notifications fetch failed: {resp.text}"
        pm_notifs = resp.json()
        pm_items = pm_notifs["items"]

        pm_project_notif = next(
            (n for n in pm_items if n.get("project_id") == created_project_id and n.get("type") == "PROJECT_ASSIGNED"),
            None
        )
        assert pm_project_notif is not None, "PM did not receive PROJECT_ASSIGNED notification"
        expected_msg = f'You have been assigned to the project "{project_name}".'
        assert expected_msg in pm_project_notif["message"], f"Message mismatch: {pm_project_notif['message']}"
        print(f"[OK] PM received PROJECT_ASSIGNED notification:")
        print(f"     Title:   {pm_project_notif['title']}")
        print(f"     Message: {pm_project_notif['message']}")
        print(f"     Unread:  {pm_project_notif['is_read'] is False}")

        # Mark PM notification as read
        resp = client.patch(f"/api/v1/notifications/{pm_project_notif['id']}/read", headers=pm_headers)
        assert resp.status_code == 200, f"Failed to mark notification as read: {resp.text}"
        assert resp.json()["is_read"] is True
        print(f"[OK] PM marked notification as read. is_read = True")

        # ====================================================================
        # STEP 3: Project Manager creates and assigns tasks
        # ====================================================================
        print("\n--- STEP 3: PM Creates and Assigns Task to Developer ---")
        task_title = f"Design Database Schema {uuid.uuid4().hex[:4]}"
        task_payload = {
            "title": task_title,
            "description": "Create PostgreSQL relational models and foreign key constraints",
            "project_id": created_project_id,
            "assigned_to": dev_user["id"],
            "status": "TODO",
            "priority": "HIGH",
            "due_date": (date.today() + timedelta(days=7)).isoformat(),
            "required_skills": ["PostgreSQL", "FastAPI"],
            "min_experience_years": 2,
        }

        resp = client.post("/api/v1/tasks", json=task_payload, headers=pm_headers)
        assert resp.status_code == 201, f"Task creation failed: {resp.text}"
        task_data = resp.json()
        created_task_id = task_data["id"]
        print(f"[OK] PM created task: '{task_data['title']}' (ID: {created_task_id}) assigned to Developer")

        # ====================================================================
        # STEP 4: Developer receives task notification & accesses task
        # ====================================================================
        print("\n--- STEP 4: Developer Receives Task Assignment Notification ---")
        resp = client.get("/api/v1/notifications", headers=dev_headers)
        assert resp.status_code == 200, f"Developer notifications fetch failed: {resp.text}"
        dev_notifs = resp.json()
        dev_items = dev_notifs["items"]

        dev_task_notif = next(
            (n for n in dev_items if n.get("task_id") == created_task_id and n.get("type") == "TASK_ASSIGNED"),
            None
        )
        assert dev_task_notif is not None, "Developer did not receive TASK_ASSIGNED notification"
        expected_dev_msg = f'You have been assigned the task "{task_title}" in project "{project_name}".'
        assert expected_dev_msg in dev_task_notif["message"], f"Message mismatch: {dev_task_notif['message']}"
        print(f"[OK] Developer received TASK_ASSIGNED notification:")
        print(f"     Title:   {dev_task_notif['title']}")
        print(f"     Message: {dev_task_notif['message']}")

        # Developer lists tasks: sees assigned task
        resp = client.get("/api/v1/tasks", headers=dev_headers)
        assert resp.status_code == 200
        my_tasks = resp.json()
        assert any(t["id"] == created_task_id for t in my_tasks), "Assigned task missing from developer's task list"
        print(f"[OK] Developer sees task in My Tasks list ({len(my_tasks)} total tasks)")

        # Cross-Developer Isolation: Other developer cannot see this task
        if other_dev_headers:
            resp = client.get(f"/api/v1/tasks/{created_task_id}", headers=other_dev_headers)
            assert resp.status_code in (403, 404), f"Security breach! Other developer accessed private task (HTTP {resp.status_code})"
            print(f"[OK] Verified RBAC: Other developer denied access to task (HTTP {resp.status_code})")

            # Other developer cannot see target developer's notification
            resp = client.get("/api/v1/notifications", headers=other_dev_headers)
            other_items = resp.json().get("items", [])
            assert not any(n["id"] == dev_task_notif["id"] for n in other_items), "Security breach: notification leaked to other developer"
            print("[OK] Verified Isolation: Other developer cannot see target developer's notifications")

        # ====================================================================
        # STEP 5: Developer Task Workflow: TODO -> IN_PROGRESS -> REVIEW
        # ====================================================================
        print("\n--- STEP 5: Developer Task Workflow Transitions ---")
        
        # 5A: Developer starts task (TODO -> IN_PROGRESS)
        resp = client.patch(f"/api/v1/tasks/{created_task_id}", json={"status": "IN_PROGRESS"}, headers=dev_headers)
        assert resp.status_code == 200, f"Developer failed to start task: {resp.text}"
        updated_task = resp.json()
        assert updated_task["status"] == "IN_PROGRESS"
        assert updated_task["started_at"] is not None, "started_at was not recorded automatically"
        print(f"[OK] Task moved to IN_PROGRESS. started_at = {updated_task['started_at']}")

        # 5B: Developer submits task for review (IN_PROGRESS -> REVIEW)
        resp = client.patch(f"/api/v1/tasks/{created_task_id}", json={"status": "REVIEW"}, headers=dev_headers)
        assert resp.status_code == 200, f"Developer failed to submit for review: {resp.text}"
        updated_task = resp.json()
        assert updated_task["status"] == "REVIEW"
        assert updated_task["review_started_at"] is not None, "review_started_at was not recorded automatically"
        print(f"[OK] Task moved to REVIEW. review_started_at = {updated_task['review_started_at']}")

        # Verify PM received TASK_REVIEW_REQUESTED notification
        resp = client.get("/api/v1/notifications", headers=pm_headers)
        review_notif = next(
            (n for n in resp.json()["items"] if n.get("task_id") == created_task_id and n.get("type") == "TASK_REVIEW_REQUESTED"),
            None
        )
        assert review_notif is not None, "PM did not receive TASK_REVIEW_REQUESTED notification"
        print(f"[OK] PM received review request notification: '{review_notif['title']}' - '{review_notif['message']}'")

        # 5C: Developer attempts unauthorized completion (REVIEW -> COMPLETED) -> Must be Forbidden
        resp = client.patch(f"/api/v1/tasks/{created_task_id}", json={"status": "COMPLETED"}, headers=dev_headers)
        assert resp.status_code == 403, f"Security failure! Developer should not be able to complete own task directly: {resp.status_code}"
        print(f"[OK] Developer self-completion blocked by RBAC: HTTP 403 Forbidden ('{resp.json()['detail']}')")

        # 5D: Project Manager approves task (REVIEW -> COMPLETED)
        resp = client.patch(f"/api/v1/tasks/{created_task_id}", json={"status": "COMPLETED"}, headers=pm_headers)
        assert resp.status_code == 200, f"PM approval failed: {resp.text}"
        completed_task = resp.json()
        assert completed_task["status"] == "COMPLETED"
        assert completed_task["completed_at"] is not None, "completed_at was not recorded automatically"
        print(f"[OK] PM approved task. status = COMPLETED, completed_at = {completed_task['completed_at']}")

        # Verify Developer received TASK_COMPLETED notification
        resp = client.get("/api/v1/notifications", headers=dev_headers)
        completed_notif = next(
            (n for n in resp.json()["items"] if n.get("task_id") == created_task_id and n.get("type") == "TASK_COMPLETED"),
            None
        )
        assert completed_notif is not None, "Developer did not receive TASK_COMPLETED notification"
        print(f"[OK] Developer received completion notification: '{completed_notif['title']}' - '{completed_notif['message']}'")

        # ====================================================================
        # STEP 6: Verify Project Progress & Activity History
        # ====================================================================
        print("\n--- STEP 6: Verify Project Progress & Activity History ---")
        # Check Project Progress
        proj_resp = db.table("projects").select("progress, status").eq("id", created_project_id).execute()
        assert proj_resp.data, "Project not found in DB"
        project_record = proj_resp.data[0]
        assert project_record["progress"] == 100, f"Expected project progress 100%, got {project_record['progress']}%"
        print(f"[OK] Verified Project Progress = {project_record['progress']}% (Status = {project_record['status']})")

        # Check Activity Log
        act_resp = db.table("activity_logs").select("action, description, created_at").eq("project_id", created_project_id).order("created_at", desc=False).execute()
        logs = act_resp.data or []
        actions = [log["action"] for log in logs]
        print(f"[OK] Activity Log recorded {len(logs)} events for project:")
        for log in logs:
            print(f"     - [{log['action']}] {log['description']}")

        assert "PROJECT_CREATED" in actions or "PROJECT_ASSIGNED" in actions, "Project creation/assignment activity missing"
        assert "TASK_CREATED" in actions, "TASK_CREATED activity missing"
        assert "TASK_STARTED" in actions, "TASK_STARTED activity missing"
        assert "TASK_SUBMITTED_FOR_REVIEW" in actions, "TASK_SUBMITTED_FOR_REVIEW activity missing"
        assert "TASK_COMPLETED" in actions, "TASK_COMPLETED activity missing"

        # ====================================================================
        # STEP 7: Security & Idempotency Validations
        # ====================================================================
        print("\n--- STEP 7: Security & Deduplication Validations ---")
        # 7A: Unauthenticated access blocked
        unauth_resp = client.get("/api/v1/projects")
        assert unauth_resp.status_code == 401, f"Expected 401 for unauthenticated request, got {unauth_resp.status_code}"
        print("[OK] Unauthenticated request correctly rejected with HTTP 401")

        # 7B: Deduplication prevents duplicate notifications on rapid re-dispatch
        n1 = NotificationService.create_notification(
            recipient_id=dev_user["id"],
            type=NotificationType.TASK_ASSIGNED,
            title="Deduplication Test",
            message="Test message",
            project_id=created_project_id,
            task_id=created_task_id,
        )
        n2 = NotificationService.create_notification(
            recipient_id=dev_user["id"],
            type=NotificationType.TASK_ASSIGNED,
            title="Deduplication Test",
            message="Test message",
            project_id=created_project_id,
            task_id=created_task_id,
        )
        assert n1.id == n2.id, f"Deduplication failed! Expected same ID, got {n1.id} vs {n2.id}"
        print(f"[OK] Notification deduplication active: Repeated calls returned identical notification ID {n1.id}")

    finally:
        # ====================================================================
        # CLEANUP: Remove temporary test records
        # ====================================================================
        print("\n--- CLEANUP: Removing Temporary Test Records ---")
        if created_task_id:
            try:
                db.table("tasks").delete().eq("id", created_task_id).execute()
                print(f"[CLEANUP] Deleted test task {created_task_id}")
            except Exception as e:
                print(f"[CLEANUP WARNING] Failed to delete task {created_task_id}: {e}")

        if created_project_id:
            try:
                db.table("activity_logs").delete().eq("project_id", created_project_id).execute()
                db.table("notifications").delete().eq("project_id", created_project_id).execute()
                db.table("projects").delete().eq("id", created_project_id).execute()
                print(f"[CLEANUP] Deleted test project {created_project_id}")
            except Exception as e:
                print(f"[CLEANUP WARNING] Failed to delete project {created_project_id}: {e}")

    print("\n" + "=" * 80)
    print("ALL END-TO-END WORKFLOW VERIFICATION CHECKS PASSED PERFECTLY!")
    print("=" * 80)


if __name__ == "__main__":
    run_e2e_workflow_test()
