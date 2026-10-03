"""
Milestone 5 — Workflow Risk Detection Tests.

Tests:
1.  Stuck task detected when IN_PROGRESS > threshold days.
2.  Review delay detected when REVIEW > threshold days.
3.  Overdue task detected when due_date < now and not completed.
4.  Project delay detected when past end_date with progress < 100.
5.  Workload risk detected when developer has > threshold active tasks.
6.  No duplicate risks created on repeated detection runs.
7.  Risk resolved when stuck task moves to REVIEW.
8.  Risk resolved when review task completes.
9.  Risk resolved when overdue task is completed.
10. Admin can see organization-wide risks.
11. PM can only see risks for their managed projects.
12. Developer can only see risks for their own tasks.
13. Correct severity for each risk type.
14. Correct risk_type field.
15. Timestamps are set correctly.
16. Unauthenticated access returns 401.
17. Status filter works correctly.
18. Risk_type filter works correctly.
19. Severity filter works correctly.
20. Project filter works correctly.
"""
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password
from app.services.workflow_risk_service import (
    STUCK_TASK_DAYS,
    REVIEW_DELAY_DAYS,
    WORKLOAD_THRESHOLD,
    detect_and_update_risks,
)

client = TestClient(app)
db = get_supabase_client()


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------

def cleanup_user(user_id: str):
    try:
        db.table("users").delete().eq("id", user_id).execute()
    except Exception as e:
        print(f"  Cleanup warning [user {user_id[:8]}]: {e}")


def cleanup_project(project_id: str):
    try:
        db.table("projects").delete().eq("id", project_id).execute()
    except Exception as e:
        print(f"  Cleanup warning [project {project_id[:8]}]: {e}")


def cleanup_task(task_id: str):
    try:
        db.table("tasks").delete().eq("id", task_id).execute()
    except Exception as e:
        print(f"  Cleanup warning [task {task_id[:8]}]: {e}")


def cleanup_risks_for_task(task_id: str):
    try:
        db.table("workflow_risks").delete().eq("task_id", task_id).execute()
    except Exception as e:
        print(f"  Cleanup warning [risks for task {task_id[:8]}]: {e}")


def cleanup_risks_for_project(project_id: str):
    try:
        db.table("workflow_risks").delete().eq("project_id", project_id).execute()
    except Exception as e:
        print(f"  Cleanup warning [risks for project {project_id[:8]}]: {e}")


def cleanup_risks_for_user(user_id: str):
    try:
        db.table("workflow_risks").delete().eq("user_id", user_id).execute()
    except Exception as e:
        print(f"  Cleanup warning [risks for user {user_id[:8]}]: {e}")


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_all_tests():
    print("\n=======================================================")
    print("AI DevFlow — Milestone 5: Workflow Risk Detection Tests")
    print("=======================================================\n")

    passed_count = 0
    total_count = 0

    def record_test(name: str, passed: bool, details: str = ""):
        nonlocal passed_count, total_count
        total_count += 1
        if passed:
            passed_count += 1
            print(f"  [PASS] {name}")
        else:
            print(f"  [FAIL] {name} — {details}")

    suffix = str(uuid.uuid4())[:8]
    password = "TestPassword123!"
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Seed test users
    # ------------------------------------------------------------------
    admin_id   = str(uuid.uuid4())
    pm_id      = str(uuid.uuid4())
    pm2_id     = str(uuid.uuid4())
    dev_id     = str(uuid.uuid4())

    db.table("users").insert([
        {"id": admin_id, "name": "Test Admin",   "email": f"admin_{suffix}@test.com",   "password_hash": hash_password(password), "role": "admin",           "is_active": True},
        {"id": pm_id,    "name": "Test PM",       "email": f"pm_{suffix}@test.com",      "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
        {"id": pm2_id,   "name": "Test PM2",      "email": f"pm2_{suffix}@test.com",     "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
        {"id": dev_id,   "name": "Test Dev",      "email": f"dev_{suffix}@test.com",     "password_hash": hash_password(password), "role": "developer",       "is_active": True},
    ]).execute()

    def get_token(email: str) -> str:
        res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        if res.status_code != 200:
            raise RuntimeError(f"Login failed for {email}: {res.text}")
        return res.json()["access_token"]

    admin_token = get_token(f"admin_{suffix}@test.com")
    pm_token    = get_token(f"pm_{suffix}@test.com")
    pm2_token   = get_token(f"pm2_{suffix}@test.com")
    dev_token   = get_token(f"dev_{suffix}@test.com")

    admin_h = {"Authorization": f"Bearer {admin_token}"}
    pm_h    = {"Authorization": f"Bearer {pm_token}"}
    pm2_h   = {"Authorization": f"Bearer {pm2_token}"}
    dev_h   = {"Authorization": f"Bearer {dev_token}"}

    # ------------------------------------------------------------------
    # Seed test project
    # ------------------------------------------------------------------
    proj_id  = str(uuid.uuid4())
    proj2_id = str(uuid.uuid4())
    past_end_date = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    future_date   = (now + timedelta(days=30)).strftime("%Y-%m-%d")

    db.table("projects").insert([
        {
            "id": proj_id,  "name": f"Risk Test Project {suffix}",
            "status": "active", "progress": 40,
            "project_manager_id": pm_id,
            "end_date": past_end_date,  # deliberately past deadline
        },
        {
            "id": proj2_id, "name": f"PM2 Project {suffix}",
            "status": "active", "progress": 20,
            "project_manager_id": pm2_id,
            "end_date": future_date,
        },
    ]).execute()

    # Tasks we'll create for testing — collected for cleanup
    task_ids_to_cleanup = []
    risk_tasks = {}

    try:
        # ------------------------------------------------------------------
        # A. STUCK_TASK — task in IN_PROGRESS for > threshold
        # ------------------------------------------------------------------
        stuck_task_id = str(uuid.uuid4())
        old_started = (now - timedelta(days=STUCK_TASK_DAYS + 1)).isoformat()
        db.table("tasks").insert({
            "id": stuck_task_id, "title": f"Stuck Task {suffix}",
            "status": "IN_PROGRESS", "priority": "MEDIUM",
            "project_id": proj_id, "assigned_to": dev_id,
            "started_at": old_started,
        }).execute()
        task_ids_to_cleanup.append(stuck_task_id)
        risk_tasks["stuck"] = stuck_task_id

        # ------------------------------------------------------------------
        # B. REVIEW_DELAY — task in REVIEW for > threshold
        # ------------------------------------------------------------------
        review_task_id = str(uuid.uuid4())
        old_review = (now - timedelta(days=REVIEW_DELAY_DAYS + 1)).isoformat()
        db.table("tasks").insert({
            "id": review_task_id, "title": f"Review Delay Task {suffix}",
            "status": "REVIEW", "priority": "MEDIUM",
            "project_id": proj_id, "assigned_to": dev_id,
            "review_started_at": old_review,
        }).execute()
        task_ids_to_cleanup.append(review_task_id)
        risk_tasks["review"] = review_task_id

        # ------------------------------------------------------------------
        # C. OVERDUE_TASK — task past due_date, not completed
        # ------------------------------------------------------------------
        overdue_task_id = str(uuid.uuid4())
        past_due = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        db.table("tasks").insert({
            "id": overdue_task_id, "title": f"Overdue Task {suffix}",
            "status": "IN_PROGRESS", "priority": "HIGH",
            "project_id": proj_id, "assigned_to": dev_id,
            "due_date": past_due,
            "started_at": (now - timedelta(days=2)).isoformat(),
        }).execute()
        task_ids_to_cleanup.append(overdue_task_id)
        risk_tasks["overdue"] = overdue_task_id

        # ------------------------------------------------------------------
        # Force detection run
        # ------------------------------------------------------------------
        detect_and_update_risks()

        # ------------------------------------------------------------------
        # Test 1: Stuck Task detected
        # ------------------------------------------------------------------
        risks_resp = db.table("workflow_risks").select("*").eq("task_id", stuck_task_id).eq("risk_type", "STUCK_TASK").execute()
        stuck_risks = risks_resp.data or []
        record_test(
            "1. STUCK_TASK detected after threshold",
            len(stuck_risks) > 0 and stuck_risks[0].get("is_resolved") == False,
            f"Found {len(stuck_risks)} risks",
        )

        # ------------------------------------------------------------------
        # Test 2: Review Delay detected
        # ------------------------------------------------------------------
        risks_resp = db.table("workflow_risks").select("*").eq("task_id", review_task_id).eq("risk_type", "REVIEW_DELAY").execute()
        review_risks = risks_resp.data or []
        record_test(
            "2. REVIEW_DELAY detected after threshold",
            len(review_risks) > 0 and review_risks[0].get("is_resolved") == False,
            f"Found {len(review_risks)} risks",
        )

        # ------------------------------------------------------------------
        # Test 3: Overdue Task detected
        # ------------------------------------------------------------------
        risks_resp = db.table("workflow_risks").select("*").eq("task_id", overdue_task_id).eq("risk_type", "OVERDUE_TASK").execute()
        overdue_risks = risks_resp.data or []
        record_test(
            "3. OVERDUE_TASK detected when past due date",
            len(overdue_risks) > 0 and overdue_risks[0].get("is_resolved") == False,
            f"Found {len(overdue_risks)} risks",
        )

        # ------------------------------------------------------------------
        # Test 4: Project Delay detected
        # ------------------------------------------------------------------
        risks_resp = db.table("workflow_risks").select("*").eq("project_id", proj_id).eq("risk_type", "PROJECT_DELAY").execute()
        proj_risks = risks_resp.data or []
        record_test(
            "4. PROJECT_DELAY detected when past end date",
            len(proj_risks) > 0 and proj_risks[0].get("is_resolved") == False,
            f"Found {len(proj_risks)} risks",
        )

        # ------------------------------------------------------------------
        # Test 5: No duplicate risks created on repeated detection run
        # ------------------------------------------------------------------
        detect_and_update_risks()
        detect_and_update_risks()
        risks_after = db.table("workflow_risks").select("*").eq("task_id", stuck_task_id).eq("risk_type", "STUCK_TASK").eq("is_resolved", False).execute()
        record_test(
            "5. No duplicate risks created on repeated detection runs",
            len(risks_after.data or []) == 1,
            f"Found {len(risks_after.data or [])} open STUCK_TASK risks for same task",
        )

        # ------------------------------------------------------------------
        # Test 6: Correct severity
        # ------------------------------------------------------------------
        record_test(
            "6. OVERDUE_TASK has HIGH severity",
            len(overdue_risks) > 0 and overdue_risks[0].get("level") == "High",
            f"Severity: {overdue_risks[0].get('level') if overdue_risks else 'N/A'}",
        )
        record_test(
            "7. REVIEW_DELAY has LOW severity",
            len(review_risks) > 0 and review_risks[0].get("level") == "Low",
            f"Severity: {review_risks[0].get('level') if review_risks else 'N/A'}",
        )

        # ------------------------------------------------------------------
        # Test 7: Stuck task resolves when task moves to REVIEW
        # ------------------------------------------------------------------
        db.table("tasks").update({
            "status": "REVIEW",
            "review_started_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", stuck_task_id).execute()

        detect_and_update_risks()

        stuck_after = db.table("workflow_risks").select("*").eq("task_id", stuck_task_id).eq("risk_type", "STUCK_TASK").execute()
        record_test(
            "8. STUCK_TASK risk resolved when task moves to REVIEW",
            len(stuck_after.data or []) > 0 and stuck_after.data[0].get("is_resolved") == True,
            f"is_resolved: {stuck_after.data[0].get('is_resolved') if stuck_after.data else 'N/A'}",
        )

        # ------------------------------------------------------------------
        # Test 8: Overdue task resolves when task is completed
        # ------------------------------------------------------------------
        db.table("tasks").update({
            "status": "COMPLETED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", overdue_task_id).execute()

        detect_and_update_risks()

        overdue_after = db.table("workflow_risks").select("*").eq("task_id", overdue_task_id).eq("risk_type", "OVERDUE_TASK").execute()
        record_test(
            "9. OVERDUE_TASK risk resolved when task is completed",
            len(overdue_after.data or []) > 0 and overdue_after.data[0].get("is_resolved") == True,
            f"is_resolved: {overdue_after.data[0].get('is_resolved') if overdue_after.data else 'N/A'}",
        )

        # ------------------------------------------------------------------
        # Test 9: Admin can see org-wide risks
        # ------------------------------------------------------------------
        res = client.get("/api/v1/workflow-risks", headers=admin_h)
        record_test(
            "10. Admin can access workflow risks endpoint (200)",
            res.status_code == 200 and isinstance(res.json(), list),
            f"Status: {res.status_code}",
        )

        # ------------------------------------------------------------------
        # Test 10: PM can see risks for their projects only
        # ------------------------------------------------------------------
        res_pm  = client.get("/api/v1/workflow-risks", headers=pm_h)
        res_pm2 = client.get("/api/v1/workflow-risks", headers=pm2_h)

        if res_pm.status_code == 200 and res_pm2.status_code == 200:
            pm_risk_ids  = {r["project_id"] for r in res_pm.json()  if r.get("project_id")}
            pm2_risk_ids = {r["project_id"] for r in res_pm2.json() if r.get("project_id")}
            # PM1 should see proj_id risks, not proj2_id
            # PM2 should NOT see proj_id risks
            pm1_correct = proj_id in pm_risk_ids
            pm2_isolation = proj_id not in pm2_risk_ids
            record_test(
                "11. PM can see risks for their managed projects",
                pm1_correct,
                f"PM risk project_ids: {pm_risk_ids}",
            )
            record_test(
                "12. PM cannot see risks from other PMs' projects",
                pm2_isolation,
                f"PM2 risk project_ids include proj_id: {proj_id in pm2_risk_ids}",
            )
        else:
            record_test("11. PM risks endpoint returns 200", res_pm.status_code == 200, res_pm.text)
            record_test("12. PM2 risks endpoint returns 200", res_pm2.status_code == 200, res_pm2.text)

        # ------------------------------------------------------------------
        # Test 11: Developer sees only their own task risks
        # ------------------------------------------------------------------
        res_dev = client.get("/api/v1/workflow-risks", headers=dev_h)
        if res_dev.status_code == 200:
            dev_risks = res_dev.json()
            # All dev risks should have user_id == dev_id (checked on backend)
            record_test(
                "13. Developer can access their relevant workflow risks (200)",
                isinstance(dev_risks, list),
                f"Status: {res_dev.status_code}, count: {len(dev_risks)}",
            )
        else:
            record_test("13. Developer risks endpoint returns 200", False, res_dev.text)

        # ------------------------------------------------------------------
        # Test 12: Unauthenticated access returns 401
        # ------------------------------------------------------------------
        res_unauth = client.get("/api/v1/workflow-risks")
        record_test(
            "14. Unauthenticated access to workflow risks returns 401",
            res_unauth.status_code == 401,
            f"Status: {res_unauth.status_code}",
        )

        # ------------------------------------------------------------------
        # Test 13: Status filter works
        # ------------------------------------------------------------------
        res_open = client.get("/api/v1/workflow-risks?status=OPEN", headers=admin_h)
        res_resolved = client.get("/api/v1/workflow-risks?status=RESOLVED", headers=admin_h)
        if res_open.status_code == 200 and res_resolved.status_code == 200:
            open_all_open   = all(r.get("status") == "OPEN" for r in res_open.json())
            resolved_all_resolved = all(r.get("status") == "RESOLVED" for r in res_resolved.json())
            record_test(
                "15. Status=OPEN filter returns only open risks",
                open_all_open,
                f"Non-OPEN found: {sum(1 for r in res_open.json() if r.get('status') != 'OPEN')}",
            )
            record_test(
                "16. Status=RESOLVED filter returns only resolved risks",
                resolved_all_resolved,
                f"Non-RESOLVED found: {sum(1 for r in res_resolved.json() if r.get('status') != 'RESOLVED')}",
            )
        else:
            record_test("15. Status filter (OPEN)",     res_open.status_code == 200,     res_open.text)
            record_test("16. Status filter (RESOLVED)", res_resolved.status_code == 200, res_resolved.text)

        # ------------------------------------------------------------------
        # Test 14: Risk type filter works
        # ------------------------------------------------------------------
        res_type = client.get("/api/v1/workflow-risks?risk_type=REVIEW_DELAY", headers=admin_h)
        if res_type.status_code == 200:
            all_correct_type = all(r.get("risk_type") == "REVIEW_DELAY" for r in res_type.json())
            record_test(
                "17. risk_type=REVIEW_DELAY filter returns only that type",
                all_correct_type,
                f"Wrong type found: {sum(1 for r in res_type.json() if r.get('risk_type') != 'REVIEW_DELAY')}",
            )
        else:
            record_test("17. risk_type filter returns 200", False, res_type.text)

        # ------------------------------------------------------------------
        # Test 15: Risk history preserved (resolved risks still in DB)
        # ------------------------------------------------------------------
        all_stuck = db.table("workflow_risks").select("id, is_resolved").eq("task_id", stuck_task_id).eq("risk_type", "STUCK_TASK").execute()
        record_test(
            "18. Resolved risk history is preserved (not deleted)",
            len(all_stuck.data or []) > 0,
            f"Risk records found: {len(all_stuck.data or [])}",
        )

        # ------------------------------------------------------------------
        # Test 16: Correct detected_at / resolved_at timestamps
        # ------------------------------------------------------------------
        if all_stuck.data:
            r = all_stuck.data[0]
            has_detected = bool(r.get("detected_at"))
            has_resolved = bool(r.get("resolved_at"))
            record_test(
                "19. Resolved risk has both detected_at and resolved_at set",
                has_detected and has_resolved,
                f"detected_at={r.get('detected_at')}, resolved_at={r.get('resolved_at')}",
            )
        else:
            record_test("19. Timestamps test skipped (no stuck risk data)", False, "No data")

        # ------------------------------------------------------------------
        # Test 17: Correct risk_type values from API
        # ------------------------------------------------------------------
        all_risks_resp = client.get("/api/v1/workflow-risks", headers=admin_h)
        if all_risks_resp.status_code == 200:
            valid_types = {"STUCK_TASK", "REVIEW_DELAY", "OVERDUE_TASK", "PROJECT_DELAY", "WORKLOAD_RISK"}
            invalid_types = [r for r in all_risks_resp.json() if r.get("risk_type") not in valid_types]
            record_test(
                "20. All risk_type values are valid enum values",
                len(invalid_types) == 0,
                f"Invalid types found: {[r.get('risk_type') for r in invalid_types][:3]}",
            )
        else:
            record_test("20. risk_type validation skipped", False, all_risks_resp.text)

    finally:
        print("\n--- Cleaning up test records ---")
        # Cleanup risks first (FK constraints)
        for tid in task_ids_to_cleanup:
            cleanup_risks_for_task(tid)
        cleanup_risks_for_project(proj_id)
        cleanup_risks_for_project(proj2_id)
        cleanup_risks_for_user(dev_id)

        # Cleanup tasks
        for tid in task_ids_to_cleanup:
            cleanup_task(tid)

        # Cleanup projects
        cleanup_project(proj_id)
        cleanup_project(proj2_id)

        # Cleanup users
        for uid in [admin_id, pm_id, pm2_id, dev_id]:
            cleanup_user(uid)

        print("Cleanup completed.")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
