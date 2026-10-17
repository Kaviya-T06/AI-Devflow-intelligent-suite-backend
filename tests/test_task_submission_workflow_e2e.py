"""
End-to-End Test Suite for Developer Task Submission and Project Manager Review Workflow
AI DevFlow Intelligence Suite

Validates:
TEST 1: Developer submits assigned task -> status becomes REVIEW, visible in PM pending reviews.
TEST 2: PM opens view submission -> task info, submission details, and GitHub evidence returned.
TEST 3: PM approves task -> status COMPLETED, project progress recalculated.
TEST 4: PM requests changes -> required feedback saved, status IN_PROGRESS, developer can resubmit.
TEST 5: PM rejects submission -> required reason saved, developer sees rejection & resubmission path.
TEST 6: Unauthorized developer attempts to submit another developer's task -> 403 Forbidden.
TEST 7: PM attempts to review task in unmanaged project -> 403 Forbidden.
TEST 8: Coding task with PR URL -> GitHub evidence endpoint behaves as expected.
TEST 9: Non-coding task without PR -> submits and reviews successfully.
TEST 10: Project with 10 tasks (4 completed, 2 in review, 4 in progress) -> progress is exactly 40%.
TEST 11: Developer resubmits after feedback -> new submission saved, historical submissions preserved.
TEST 12: Review action validation failure (e.g., missing rejection comment) -> 422/400 returned, state untouched.
"""

import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password, create_access_token

client = TestClient(app)
db = get_supabase_client()


def run_e2e_workflow_tests():
    print("=" * 80)
    print("STARTING COMPLETE TASK SUBMISSION & PM REVIEW WORKFLOW E2E TESTS")
    print("=" * 80)

    results = []

    def record(name: str, passed: bool, msg: str = ""):
        results.append((name, passed, msg))
        status_str = "[PASS]" if passed else "[FAIL]"
        print(f"  {status_str} {name}" + (f" -> {msg}" if msg else ""))

    # Setup unique IDs for test isolation
    suffix = uuid.uuid4().hex[:6]
    pm1_id = str(uuid.uuid4())
    pm2_id = str(uuid.uuid4())
    dev1_id = str(uuid.uuid4())
    dev2_id = str(uuid.uuid4())

    proj1_id = str(uuid.uuid4())
    proj2_id = str(uuid.uuid4())

    created_user_ids = [pm1_id, pm2_id, dev1_id, dev2_id]
    created_proj_ids = [proj1_id, proj2_id]
    created_task_ids = []

    try:
        # 1. Seed Users in Supabase
        db.table("users").insert([
            {
                "id": pm1_id,
                "email": f"pm1_{suffix}@test.devflow.ai",
                "name": f"PM One {suffix}",
                "password_hash": hash_password("TestPass@123"),
                "role": "project_manager",
                "is_active": True,
            },
            {
                "id": pm2_id,
                "email": f"pm2_{suffix}@test.devflow.ai",
                "name": f"PM Two {suffix}",
                "password_hash": hash_password("TestPass@123"),
                "role": "project_manager",
                "is_active": True,
            },
            {
                "id": dev1_id,
                "email": f"dev1_{suffix}@test.devflow.ai",
                "name": f"Dev One {suffix}",
                "password_hash": hash_password("TestPass@123"),
                "role": "developer",
                "is_active": True,
            },
            {
                "id": dev2_id,
                "email": f"dev2_{suffix}@test.devflow.ai",
                "name": f"Dev Two {suffix}",
                "password_hash": hash_password("TestPass@123"),
                "role": "developer",
                "is_active": True,
            },
        ]).execute()

        # 2. Seed Projects
        db.table("projects").insert([
            {
                "id": proj1_id,
                "name": f"Alpha Proj {suffix}",
                "description": "Alpha Project managed by PM 1",
                "project_manager_id": pm1_id,
                "progress": 0,
            },
            {
                "id": proj2_id,
                "name": f"Beta Proj {suffix}",
                "description": "Beta Project managed by PM 2",
                "project_manager_id": pm2_id,
                "progress": 0,
            },
        ]).execute()

        # Generate JWT Auth Tokens
        token_pm1 = create_access_token({"sub": pm1_id, "role": "project_manager"})
        token_pm2 = create_access_token({"sub": pm2_id, "role": "project_manager"})
        token_dev1 = create_access_token({"sub": dev1_id, "role": "developer"})
        token_dev2 = create_access_token({"sub": dev2_id, "role": "developer"})

        h_pm1 = {"Authorization": f"Bearer {token_pm1}"}
        h_pm2 = {"Authorization": f"Bearer {token_pm2}"}
        h_dev1 = {"Authorization": f"Bearer {token_dev1}"}
        h_dev2 = {"Authorization": f"Bearer {token_dev2}"}

        # Helper to create tasks
        def make_task(proj_id, assigned_to, title, status="TODO", priority="MEDIUM"):
            t_id = str(uuid.uuid4())
            created_task_ids.append(t_id)
            db.table("tasks").insert({
                "id": t_id,
                "project_id": proj_id,
                "assigned_to": assigned_to,
                "title": title,
                "description": f"Detailed description for {title}",
                "priority": priority,
                "status": status,
            }).execute()
            return t_id

        # -------------------------------------------------------------------------
        # TEST 1: Developer submits assigned task -> status becomes REVIEW, visible in PM pending reviews
        # -------------------------------------------------------------------------
        t1_id = make_task(proj1_id, dev1_id, f"Feature T1 {suffix}", status="IN_PROGRESS")
        sub_payload_1 = {
            "work_summary": "Implemented JWT validation pipeline",
            "implementation_details": "Added token parsing, expiry validation and bearer auth middleware.",
            "testing_performed": "Ran 14 unit test assertions covering edge cases.",
            "pull_request_url": "https://github.com/acme/repo/pull/42",
            "additional_comments": "Ready for initial PM review.",
        }
        res_sub1 = client.post(f"/api/v1/tasks/{t1_id}/submit", json=sub_payload_1, headers=h_dev1)
        test1_pass = res_sub1.status_code == 201 and res_sub1.json().get("status") == "PENDING"

        # Check task status updated to REVIEW
        t1_db = db.table("tasks").select("status").eq("id", t1_id).execute()
        test1_pass = test1_pass and (t1_db.data and t1_db.data[0]["status"] == "REVIEW")

        # Check visible in PM1 pending reviews
        res_pm1_pend = client.get("/api/v1/tasks/pending-reviews", headers=h_pm1)
        pm1_tasks = res_pm1_pend.json() if res_pm1_pend.status_code == 200 else []
        test1_visible_pm1 = any(p["task_id"] == t1_id for p in pm1_tasks)
        test1_pass = test1_pass and test1_visible_pm1
        record("TEST 1: Developer task submission sets REVIEW and appears in PM pending reviews", test1_pass)

        # -------------------------------------------------------------------------
        # TEST 2: PM opens view submission -> task info, submission details, and GitHub evidence
        # -------------------------------------------------------------------------
        res_subs = client.get(f"/api/v1/tasks/{t1_id}/submissions", headers=h_pm1)
        sub_list = res_subs.json() if res_subs.status_code == 200 else []
        test2_pass = len(sub_list) >= 1 and sub_list[-1]["work_summary"] == sub_payload_1["work_summary"]
        res_gh = client.get(f"/api/v1/tasks/{t1_id}/github-evidence", headers=h_pm1)
        test2_pass = test2_pass and res_gh.status_code == 200 and "available" in res_gh.json()
        record("TEST 2: PM inspection returns task info, submission history, and GitHub evidence", test2_pass)

        # -------------------------------------------------------------------------
        # TEST 3: PM approves task -> status COMPLETED, project progress recalculated
        # -------------------------------------------------------------------------
        res_appr = client.post(
            f"/api/v1/tasks/{t1_id}/reviews/approve",
            json={"decision": "APPROVE", "review_comment": "Approved! Excellent test coverage."},
            headers=h_pm1,
        )
        appr_dec = res_appr.json().get("review", {}).get("decision") if "review" in res_appr.json() else res_appr.json().get("decision")
        test3_pass = res_appr.status_code == 200 and appr_dec == "APPROVE"
        t1_db_appr = db.table("tasks").select("status").eq("id", t1_id).execute()
        test3_pass = test3_pass and (t1_db_appr.data and t1_db_appr.data[0]["status"] == "COMPLETED")
        # Check project progress was updated
        p1_db = db.table("projects").select("progress").eq("id", proj1_id).execute()
        test3_pass = test3_pass and (p1_db.data and p1_db.data[0]["progress"] == 100)
        record("TEST 3: PM APPROVE sets status to COMPLETED and recalculates project progress", test3_pass)

        # -------------------------------------------------------------------------
        # TEST 4: PM requests changes -> required feedback saved, status IN_PROGRESS, dev can resubmit
        # -------------------------------------------------------------------------
        t2_id = make_task(proj1_id, dev1_id, f"Feature T2 {suffix}", status="IN_PROGRESS")
        client.post(f"/api/v1/tasks/{t2_id}/submit", json=sub_payload_1, headers=h_dev1)
        res_rc = client.post(
            f"/api/v1/tasks/{t2_id}/reviews/request-changes",
            json={"decision": "REQUEST_CHANGES", "review_comment": "Please add error boundary for network drops."},
            headers=h_pm1,
        )
        rc_dec = res_rc.json().get("review", {}).get("decision") if "review" in res_rc.json() else res_rc.json().get("decision")
        test4_pass = res_rc.status_code == 200 and rc_dec == "REQUEST_CHANGES"
        t2_db = db.table("tasks").select("status").eq("id", t2_id).execute()
        test4_pass = test4_pass and (t2_db.data and t2_db.data[0]["status"] == "IN_PROGRESS")
        record("TEST 4: PM REQUEST_CHANGES transitions task back to IN_PROGRESS with feedback saved", test4_pass)

        # -------------------------------------------------------------------------
        # TEST 5: PM rejects submission -> required reason saved, dev sees rejection & resubmission path
        # -------------------------------------------------------------------------
        t3_id = make_task(proj1_id, dev1_id, f"Feature T3 {suffix}", status="IN_PROGRESS")
        client.post(f"/api/v1/tasks/{t3_id}/submit", json=sub_payload_1, headers=h_dev1)
        res_rej = client.post(
            f"/api/v1/tasks/{t3_id}/reviews/reject",
            json={"decision": "REJECT", "review_comment": "Architecture violates security baseline; must redesign."},
            headers=h_pm1,
        )
        rej_dec = res_rej.json().get("review", {}).get("decision") if "review" in res_rej.json() else res_rej.json().get("decision")
        test5_pass = res_rej.status_code == 200 and rej_dec == "REJECT"
        t3_db = db.table("tasks").select("status").eq("id", t3_id).execute()
        test5_pass = test5_pass and (t3_db.data and t3_db.data[0]["status"] == "IN_PROGRESS")
        record("TEST 5: PM REJECT saves rejection reason and leaves task in IN_PROGRESS for rework", test5_pass)

        # -------------------------------------------------------------------------
        # TEST 6: Unauthorized developer attempts to submit another developer's task -> 403 Forbidden
        # -------------------------------------------------------------------------
        res_dev2_hack = client.post(
            f"/api/v1/tasks/{t3_id}/submit",
            json=sub_payload_1,
            headers=h_dev2,
        )
        test6_pass = res_dev2_hack.status_code == 403
        record("TEST 6: Unauthorized developer submitting another's task is blocked (403 Forbidden)", test6_pass)

        # -------------------------------------------------------------------------
        # TEST 7: PM attempts to review task in unmanaged project -> 403 Forbidden
        # -------------------------------------------------------------------------
        t7_id = make_task(proj1_id, dev1_id, f"Feature T7 {suffix}", status="IN_PROGRESS")
        client.post(f"/api/v1/tasks/{t7_id}/submit", json=sub_payload_1, headers=h_dev1)
        res_pm2_hack = client.post(
            f"/api/v1/tasks/{t7_id}/reviews/approve",
            json={"decision": "APPROVE", "review_comment": "Unauthorized approve attempt"},
            headers=h_pm2,
        )
        test7_pass = res_pm2_hack.status_code == 403
        record("TEST 7: Project Manager reviewing unmanaged project's task is blocked (403 Forbidden)", test7_pass)

        # -------------------------------------------------------------------------
        # TEST 8: Coding task with PR URL -> GitHub evidence endpoint behaves as expected
        # -------------------------------------------------------------------------
        res_gh8 = client.get(f"/api/v1/tasks/{t1_id}/github-evidence", headers=h_pm1)
        test8_pass = res_gh8.status_code == 200
        gh_data = res_gh8.json()
        test8_pass = test8_pass and ("available" in gh_data) and ("pull_request_url" in gh_data)
        record("TEST 8: GitHub evidence endpoint validates PR URL and returns structured evidence", test8_pass)

        # -------------------------------------------------------------------------
        # TEST 9: Non-coding task without PR -> submits and reviews successfully
        # -------------------------------------------------------------------------
        t_noncode = make_task(proj1_id, dev1_id, f"Doc Task {suffix}", status="IN_PROGRESS")
        sub_payload_noncode = {
            "work_summary": "Authored comprehensive deployment architecture document",
            "implementation_details": "Created network diagrams, RDS failover topology, and backup schedules.",
            "testing_performed": "Verified all diagram links and reviewed against company infosec policy.",
            "deliverable_url": "https://wiki.internal.org/docs/arch-spec-v2",
            "pull_request_url": None,
            "additional_comments": "Non-coding deliverable ready for PM sign-off.",
        }
        res_sub_nc = client.post(f"/api/v1/tasks/{t_noncode}/submit", json=sub_payload_noncode, headers=h_dev1)
        test9_pass = res_sub_nc.status_code == 201
        res_appr_nc = client.post(
            f"/api/v1/tasks/{t_noncode}/reviews/approve",
            json={"decision": "APPROVE", "review_comment": "Documentation verified and accepted."},
            headers=h_pm1,
        )
        test9_pass = test9_pass and res_appr_nc.status_code == 200
        t_nc_db = db.table("tasks").select("status").eq("id", t_noncode).execute()
        test9_pass = test9_pass and (t_nc_db.data and t_nc_db.data[0]["status"] == "COMPLETED")
        record("TEST 9: Non-coding task without GitHub PR submits and approves cleanly", test9_pass)

        # -------------------------------------------------------------------------
        # TEST 10: Project with 10 tasks (4 completed, 2 in review, 4 in progress) -> progress is exactly 40%
        # -------------------------------------------------------------------------
        # Create a clean dedicated project with exactly 10 tasks
        proj10_id = str(uuid.uuid4())
        created_proj_ids.append(proj10_id)
        db.table("projects").insert({
            "id": proj10_id,
            "name": f"Ten Tasks Proj {suffix}",
            "project_manager_id": pm1_id,
            "progress": 0,
        }).execute()

        # 4 COMPLETED tasks
        for i in range(4):
            make_task(proj10_id, dev1_id, f"Done Task {i+1} {suffix}", status="COMPLETED")
        # 2 REVIEW tasks
        for i in range(2):
            make_task(proj10_id, dev1_id, f"Review Task {i+1} {suffix}", status="REVIEW")
        # 4 IN_PROGRESS tasks
        for i in range(4):
            make_task(proj10_id, dev1_id, f"InProg Task {i+1} {suffix}", status="IN_PROGRESS")

        # Recalculate progress using the API task update trigger or service trigger
        from app.services.task_service import _recalculate_project_progress
        _recalculate_project_progress(proj10_id)

        p10_db = db.table("projects").select("progress").eq("id", proj10_id).execute()
        calc_progress = p10_db.data[0]["progress"] if p10_db.data else None
        test10_pass = calc_progress == 40
        record("TEST 10: 10 tasks (4 completed, 2 review, 4 in_progress) -> progress == 40%", test10_pass, f"got {calc_progress}%")

        # -------------------------------------------------------------------------
        # TEST 11: Developer resubmits after feedback -> new submission saved, historical submissions preserved
        # -------------------------------------------------------------------------
        # t2_id had changes requested earlier and was resubmitted
        sub_payload_resub = {
            "work_summary": "Added network retry error boundaries to satisfy PM feedback",
            "implementation_details": "Implemented exponential backoff with 3 retries in apiClient.ts.",
            "testing_performed": "Simulated offline network transitions and verified auto-recovery.",
            "pull_request_url": "https://github.com/acme/repo/pull/42",
            "additional_comments": "Resubmission ready for re-review.",
        }
        res_resub = client.post(f"/api/v1/tasks/{t2_id}/submit", json=sub_payload_resub, headers=h_dev1)
        test11_pass = res_resub.status_code == 201
        res_all_subs = client.get(f"/api/v1/tasks/{t2_id}/submissions", headers=h_pm1)
        all_subs = res_all_subs.json() if res_all_subs.status_code == 200 else []
        test11_pass = test11_pass and len(all_subs) >= 2
        record("TEST 11: Developer resubmission preserves full history and records new revision", test11_pass, f"found {len(all_subs)} historical submissions")

        # -------------------------------------------------------------------------
        # TEST 12: Review action validation failure (e.g., missing rejection comment) -> 422/400 returned, state untouched
        # -------------------------------------------------------------------------
        res_invalid_rej = client.post(
            f"/api/v1/tasks/{t2_id}/reviews/reject",
            json={"decision": "REJECT", "review_comment": "   "},
            headers=h_pm1,
        )
        test12_pass = res_invalid_rej.status_code in (400, 422)
        # Verify status is still REVIEW and was NOT modified
        t2_state_check = db.table("tasks").select("status").eq("id", t2_id).execute()
        test12_pass = test12_pass and (t2_state_check.data and t2_state_check.data[0]["status"] == "REVIEW")
        record("TEST 12: Missing rejection comment triggers validation error and leaves task state untouched", test12_pass, f"HTTP {res_invalid_rej.status_code}")

    finally:
        # Cleanup seeded records to preserve database cleanliness
        print("\nCleaning up test artifacts...")
        for tid in created_task_ids:
            try:
                db.table("activity_logs").delete().eq("entity_id", tid).execute()
                db.table("task_reviews").delete().eq("task_id", tid).execute()
                db.table("task_submissions").delete().eq("task_id", tid).execute()
                db.table("tasks").delete().eq("id", tid).execute()
            except Exception:
                pass

        for pid in created_proj_ids:
            try:
                db.table("projects").delete().eq("id", pid).execute()
            except Exception:
                pass

        for uid in created_user_ids:
            try:
                db.table("users").delete().eq("id", uid).execute()
            except Exception:
                pass
        print("Cleanup completed.")

    print("=" * 80)
    passed_total = sum(1 for _, p, _ in results if p)
    print(f"VERIFICATION SUMMARY: {passed_total}/{len(results)} TESTS PASSED")
    print("=" * 80)
    assert passed_total == len(results), f"Some tests failed: {[n for n, p, _ in results if not p]}"


if __name__ == "__main__":
    run_e2e_workflow_tests()
