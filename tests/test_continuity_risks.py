"""
Milestone AI Continuity & Workflow Risks Context Tests.

Tests:
1. Project with open workflow risks -> AI context contains those risks.
2. Old open risk -> still appears even if it is outside the recent activity-log window.
3. Resolved risk -> correctly marked as resolved in AI context.
4. Project with no risks -> AI context explicitly indicates no open workflow risks.
5. Developer/PM/Admin authorization remains enforced.
6. Unauthorized project -> AI endpoint remains blocked (403).
7. Existing /generate endpoint works cleanly with mocked AI.
8. Existing /ask endpoint works cleanly with mocked AI.
9. Invalid/non-existent project returns 404.
10. Unauthenticated access returns 401.
"""
import os
import sys
import uuid
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password

client = TestClient(app)
db = get_supabase_client()


def cleanup_records(table: str, ids: list):
    for rec_id in ids:
        try:
            db.table(table).delete().eq("id", rec_id).execute()
        except Exception:
            pass


def run_tests():
    print("\n=======================================================")
    print("AI DevFlow -- Direct Workflow Risk Context for AI Tests")
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
            print(f"  [FAIL] {name} -- {details}")

    suffix = str(uuid.uuid4())[:8]
    password = "TestPassword123!"
    now = datetime.now(timezone.utc)

    admin_id = str(uuid.uuid4())
    pm_id = str(uuid.uuid4())
    pm2_id = str(uuid.uuid4())
    dev1_id = str(uuid.uuid4())
    dev2_id = str(uuid.uuid4())

    proj_with_risks = str(uuid.uuid4())
    proj_no_risks = str(uuid.uuid4())
    proj_pm2 = str(uuid.uuid4())

    task_dev1 = str(uuid.uuid4())

    risk_open_id = str(uuid.uuid4())
    risk_old_open_id = str(uuid.uuid4())
    risk_resolved_id = str(uuid.uuid4())

    activity_ids = []

    try:
        # Seed users
        db.table("users").insert([
            {"id": admin_id, "name": "Admin User", "email": f"admin_{suffix}@test.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
            {"id": pm_id, "name": "PM One", "email": f"pm_{suffix}@test.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": pm2_id, "name": "PM Two", "email": f"pm2_{suffix}@test.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": dev1_id, "name": "Dev One", "email": f"dev1_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
            {"id": dev2_id, "name": "Dev Two", "email": f"dev2_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
        ]).execute()

        # Seed projects
        db.table("projects").insert([
            {"id": proj_with_risks, "name": f"Project With Risks {suffix}", "project_manager_id": pm_id, "status": "active", "progress": 50},
            {"id": proj_no_risks, "name": f"Clean Project {suffix}", "project_manager_id": pm_id, "status": "active", "progress": 10},
            {"id": proj_pm2, "name": f"PM2 Project {suffix}", "project_manager_id": pm2_id, "status": "active", "progress": 20},
        ]).execute()

        # Seed task for dev1 on proj_with_risks
        db.table("tasks").insert([
            {"id": task_dev1, "title": f"Dev1 Task {suffix}", "project_id": proj_with_risks, "assigned_to": dev1_id, "status": "IN_PROGRESS", "priority": "HIGH"}
        ]).execute()

        # Seed 1 fresh open risk
        db.table("workflow_risks").insert([
            {
                "id": risk_open_id,
                "project_id": proj_with_risks,
                "task_id": task_dev1,
                "risk_type": "OVERDUE_TASK",
                "level": "High",
                "title": "Overdue Task Risk",
                "description": "Task is severely past due date",
                "status": "OPEN",
                "is_resolved": False,
                "detected_at": now.isoformat(),
            }
        ]).execute()

        # Seed 1 old open risk (detected 45 days ago)
        old_detected = (now - timedelta(days=45)).isoformat()
        db.table("workflow_risks").insert([
            {
                "id": risk_old_open_id,
                "project_id": proj_with_risks,
                "risk_type": "PROJECT_DELAY",
                "level": "Critical",
                "title": "Old Open Project Delay",
                "description": "Project milestone overdue since last month",
                "status": "OPEN",
                "is_resolved": False,
                "detected_at": old_detected,
            }
        ]).execute()

        # Seed 1 resolved risk
        db.table("workflow_risks").insert([
            {
                "id": risk_resolved_id,
                "project_id": proj_with_risks,
                "risk_type": "REVIEW_DELAY",
                "level": "Low",
                "title": "Resolved Review Delay",
                "description": "Review delay was addressed",
                "status": "RESOLVED",
                "is_resolved": True,
                "detected_at": (now - timedelta(days=10)).isoformat(),
                "resolved_at": (now - timedelta(days=2)).isoformat(),
            }
        ]).execute()

        # Seed 25 dummy activity logs so the recent activity limit (20) would push out old items
        for i in range(25):
            act_id = str(uuid.uuid4())
            activity_ids.append(act_id)
            db.table("activity_logs").insert({
                "id": act_id,
                "user_id": pm_id,
                "action": "DUMMY_ACTIVITY",
                "entity_type": "project",
                "entity_id": proj_with_risks,
                "description": f"Dummy log {i}",
                "created_at": (now - timedelta(minutes=25 - i)).isoformat()
            }).execute()

        # Auth tokens
        def get_token(email: str) -> str:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            return res.json()["access_token"]

        admin_h = {"Authorization": f"Bearer {get_token(f'admin_{suffix}@test.com')}"}
        pm_h = {"Authorization": f"Bearer {get_token(f'pm_{suffix}@test.com')}"}
        dev1_h = {"Authorization": f"Bearer {get_token(f'dev1_{suffix}@test.com')}"}
        dev2_h = {"Authorization": f"Bearer {get_token(f'dev2_{suffix}@test.com')}"}

        # Mock LLM response for generate
        mock_summary = {
            "project_overview": "Project with risks overview",
            "previous_developer_work": "None",
            "current_work": "Dev1 working on task",
            "pending_work": "None",
            "blocked_overdue_work": "Task is overdue",
            "recent_github_activity": "No GitHub data",
            "known_issues": "Project milestone overdue",
            "important_context": "Critical project",
            "what_next_developer_should_know": "Fix overdue task",
            "recommended_next_steps": "Complete task"
        }

        # TEST 1 & 2 & 3: Project with open workflow risks & old open risk & resolved risk
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            
            res = client.post(f"/api/v1/projects/{proj_with_risks}/continuity/generate", headers=pm_h)
            record_test("1. Generate endpoint returns 200 with valid structure", res.status_code == 200, res.text)
            
            # Inspect prompt received by ask_llm
            prompt_sent = mock_llm.call_args[0][0]
            
            record_test(
                "2. Context contains VERIFIED WORKFLOW RISKS section",
                "VERIFIED WORKFLOW RISKS:" in prompt_sent,
                "Section missing from prompt"
            )
            record_test(
                "3. Open risk appears in prompt with actual DB fields",
                risk_open_id in prompt_sent and "OVERDUE_TASK" in prompt_sent and "High" in prompt_sent,
                "Open risk not found in prompt"
            )
            record_test(
                "4. Old open risk (>20 activity logs ago) is present in AI context",
                risk_old_open_id in prompt_sent and "Old Open Project Delay" in prompt_sent,
                "Old open risk was lost or omitted"
            )
            record_test(
                "5. Resolved risk is properly categorized under Resolved Risks",
                "Resolved Risks:" in prompt_sent and risk_resolved_id in prompt_sent and "RESOLVED" in prompt_sent,
                "Resolved risk missing or misplaced"
            )

        # TEST 4: Project with NO risks
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            
            res = client.post(f"/api/v1/projects/{proj_no_risks}/continuity/generate", headers=pm_h)
            record_test("6. Generate for clean project returns 200", res.status_code == 200, res.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            record_test(
                "7. Context explicitly indicates no active open workflow risks",
                "No active open workflow risks detected for this project" in prompt_sent,
                "Absence of risks not explicitly stated in context"
            )

        # TEST 5 & 6: Authorization & RBAC
        # Admin can access
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            res_admin = client.post(f"/api/v1/projects/{proj_with_risks}/continuity/generate", headers=admin_h)
            record_test("8. Admin can access continuity for any project (200)", res_admin.status_code == 200, res_admin.text)

        # Assigned Developer (Dev1) can access
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            res_dev1 = client.post(f"/api/v1/projects/{proj_with_risks}/continuity/generate", headers=dev1_h)
            record_test("9. Assigned Developer can access continuity for project (200)", res_dev1.status_code == 200, res_dev1.text)

        # Unassigned Developer (Dev2) is blocked (403)
        res_dev2 = client.post(f"/api/v1/projects/{proj_with_risks}/continuity/generate", headers=dev2_h)
        record_test("10. Unassigned Developer is forbidden from project continuity (403)", res_dev2.status_code == 403, res_dev2.text)

        # PM cannot access another PM's project
        res_pm_proj2 = client.post(f"/api/v1/projects/{proj_pm2}/continuity/generate", headers=pm_h)
        record_test("11. PM cannot access another PM's project continuity (403)", res_pm_proj2.status_code == 403, res_pm_proj2.text)

        # Invalid project returns 404
        fake_id = str(uuid.uuid4())
        res_404 = client.post(f"/api/v1/projects/{fake_id}/continuity/generate", headers=admin_h)
        record_test("12. Non-existent project returns 404", res_404.status_code == 404, res_404.text)

        # Unauthenticated returns 401
        res_401 = client.post(f"/api/v1/projects/{proj_with_risks}/continuity/generate")
        record_test("13. Unauthenticated request returns 401", res_401.status_code == 401, res_401.text)

        # TEST 8: /ask endpoint works and includes risk context
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "The project is delayed due to an overdue task."
            
            res_ask = client.post(
                f"/api/v1/projects/{proj_with_risks}/continuity/ask",
                headers=pm_h,
                json={"question": "What are the major bottlenecks?"}
            )
            record_test("14. /ask endpoint returns 200 with answer", res_ask.status_code == 200 and "answer" in res_ask.json(), res_ask.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            record_test(
                "15. /ask context also receives verified workflow risks",
                "VERIFIED WORKFLOW RISKS:" in prompt_sent and risk_open_id in prompt_sent,
                "Risks missing in /ask context"
            )

    finally:
        cleanup_records("activity_logs", activity_ids)
        cleanup_records("workflow_risks", [risk_open_id, risk_old_open_id, risk_resolved_id])
        cleanup_records("tasks", [task_dev1])
        cleanup_records("projects", [proj_with_risks, proj_no_risks, proj_pm2])
        cleanup_records("users", [admin_id, pm_id, pm2_id, dev1_id, dev2_id])

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
