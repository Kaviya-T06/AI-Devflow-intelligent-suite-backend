"""
Milestone: Verify and Fix Task-Level Activity History in AI Continuity Tests.

Tests:
1. Project-level activity is included in AI context.
2. Task-level activity is included when its task belongs to the requested project (creation, assignment, start, completion).
3. Task activity from another project is strictly excluded.
4. Activity ordering is correct (newest first).
5. Duplicate events are not introduced when combining project-level and task-level logs.
6. History limit (30) is respected when many activity events exist.
7. Verified workflow risks still appear exactly once in their dedicated section.
8. Existing conversation memory still reaches the AI prompt under RECENT CONVERSATION.
9. Project RBAC is unchanged on /generate and /ask (Admin & assigned PM & assigned Dev permitted, others 403).
10. Project History endpoint (/api/v1/projects/{id}/history) returns verified project & task history.
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
from app.services.activity_service import log_activity
from app.services.continuity_service import _build_project_context

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
    print("AI DevFlow -- Task-Level Activity History in AI Continuity Tests")
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
    dev_id = str(uuid.uuid4())
    dev2_id = str(uuid.uuid4())

    proj1_id = str(uuid.uuid4())
    proj2_id = str(uuid.uuid4())

    task1_id = str(uuid.uuid4())
    task2_id = str(uuid.uuid4())
    task_other_proj_id = str(uuid.uuid4())

    risk_id = str(uuid.uuid4())

    activity_ids = []

    try:
        # 1. Seed users
        db.table("users").insert([
            {"id": admin_id, "name": f"Admin {suffix}", "email": f"admin_act_{suffix}@test.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
            {"id": pm_id, "name": f"PM One {suffix}", "email": f"pm_act_{suffix}@test.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": pm2_id, "name": f"PM Two {suffix}", "email": f"pm2_act_{suffix}@test.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": dev_id, "name": f"Dev Alice {suffix}", "email": f"dev_alice_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
            {"id": dev2_id, "name": f"Dev Bob {suffix}", "email": f"dev_bob_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
        ]).execute()

        # 2. Seed projects
        db.table("projects").insert([
            {"id": proj1_id, "name": f"Alpha Continuity Project {suffix}", "description": "Primary Project", "project_manager_id": pm_id, "status": "active", "progress": 50},
            {"id": proj2_id, "name": f"Beta Foreign Project {suffix}", "description": "Foreign Project", "project_manager_id": pm2_id, "status": "active", "progress": 10},
        ]).execute()

        # 3. Seed tasks
        db.table("tasks").insert([
            {"id": task1_id, "title": "Implement OAuth flow", "project_id": proj1_id, "assigned_to": dev_id, "status": "COMPLETED", "priority": "HIGH"},
            {"id": task2_id, "title": "Add Database Indexing", "project_id": proj1_id, "assigned_to": dev_id, "status": "IN_PROGRESS", "priority": "MEDIUM"},
            {"id": task_other_proj_id, "title": "Foreign Project Task", "project_id": proj2_id, "assigned_to": dev2_id, "status": "IN_PROGRESS", "priority": "LOW"},
        ]).execute()

        # 4. Seed workflow risk for proj1
        db.table("workflow_risks").insert([
            {
                "id": risk_id,
                "project_id": proj1_id,
                "task_id": task2_id,
                "risk_type": "STUCK_TASK",
                "level": "Medium",
                "title": "Stuck DB Indexing Task",
                "description": "Task in progress for too long",
                "status": "OPEN",
                "is_resolved": False,
                "detected_at": now.isoformat(),
            }
        ]).execute()

        # 5. Seed diverse activity logs:
        # 5a. Project-level log for proj1
        act_p1 = str(uuid.uuid4())
        activity_ids.append(act_p1)
        db.table("activity_logs").insert({
            "id": act_p1,
            "user_id": pm_id,
            "action": "PROJECT_UPDATED",
            "entity_type": "project",
            "entity_id": proj1_id,
            "project_id": proj1_id,
            "description": "Project timeline updated",
            "created_at": (now - timedelta(minutes=40)).isoformat()
        }).execute()

        # 5b. Task-level log for task1 (proj1)
        act_t1_created = str(uuid.uuid4())
        activity_ids.append(act_t1_created)
        db.table("activity_logs").insert({
            "id": act_t1_created,
            "user_id": pm_id,
            "action": "TASK_CREATED",
            "entity_type": "task",
            "entity_id": task1_id,
            "project_id": proj1_id,
            "description": "Task 'Implement OAuth flow' created",
            "created_at": (now - timedelta(minutes=35)).isoformat()
        }).execute()

        # 5c. Task-level log for task1 completed (proj1)
        act_t1_completed = str(uuid.uuid4())
        activity_ids.append(act_t1_completed)
        db.table("activity_logs").insert({
            "id": act_t1_completed,
            "user_id": dev_id,
            "action": "TASK_COMPLETED",
            "entity_type": "task",
            "entity_id": task1_id,
            "project_id": proj1_id,
            "description": "Task 'Implement OAuth flow' moved to COMPLETED",
            "created_at": (now - timedelta(minutes=20)).isoformat()
        }).execute()

        # 5d. Task-level log for task2 started (proj1)
        act_t2_started = str(uuid.uuid4())
        activity_ids.append(act_t2_started)
        db.table("activity_logs").insert({
            "id": act_t2_started,
            "user_id": dev_id,
            "action": "TASK_STARTED",
            "entity_type": "task",
            "entity_id": task2_id,
            "project_id": proj1_id,
            "description": "Task 'Add Database Indexing' moved to IN_PROGRESS",
            "created_at": (now - timedelta(minutes=10)).isoformat()
        }).execute()

        # 5e. Foreign task-level log for task_other_proj (proj2) -> MUST BE EXCLUDED from proj1 context
        act_foreign = str(uuid.uuid4())
        activity_ids.append(act_foreign)
        db.table("activity_logs").insert({
            "id": act_foreign,
            "user_id": dev2_id,
            "action": "TASK_STARTED",
            "entity_type": "task",
            "entity_id": task_other_proj_id,
            "project_id": proj2_id,
            "description": "Task 'Foreign Project Task' moved to IN_PROGRESS",
            "created_at": (now - timedelta(minutes=5)).isoformat()
        }).execute()

        # Helper to get auth token
        def get_token(email: str) -> str:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            return res.json()["access_token"]

        admin_h = {"Authorization": f"Bearer {get_token(f'admin_act_{suffix}@test.com')}"}
        pm_h = {"Authorization": f"Bearer {get_token(f'pm_act_{suffix}@test.com')}"}
        dev_h = {"Authorization": f"Bearer {get_token(f'dev_alice_{suffix}@test.com')}"}
        dev2_h = {"Authorization": f"Bearer {get_token(f'dev_bob_{suffix}@test.com')}"}

        # -------------------------------------------------------------
        # TEST 1 & 2 & 3: Direct Context Inspection via _build_project_context
        # -------------------------------------------------------------
        context_str, raw_data = _build_project_context.__wrapped__(proj1_id) if hasattr(_build_project_context, '__wrapped__') else (
            # Run async function cleanly
            __import__("asyncio").run(_build_project_context(proj1_id))
        )

        record_test(
            "1. Project-level activity is included in AI context",
            "PROJECT_UPDATED" in context_str and "Project timeline updated" in context_str,
            "Project-level event missing from context"
        )

        record_test(
            "2. Task-level activity for project tasks is included in AI context",
            "TASK_CREATED" in context_str and "TASK_COMPLETED" in context_str and "Implement OAuth flow" in context_str and "Add Database Indexing" in context_str,
            "Task-level events missing from context"
        )

        record_test(
            "3. Foreign task activity from another project is strictly excluded",
            "Foreign Project Task" not in context_str and act_foreign not in context_str,
            "Cross-project activity leaked into context"
        )

        # Check ordering: TASK_STARTED (t2, 10 min ago) should appear before TASK_COMPLETED (t1, 20 min ago) and PROJECT_UPDATED (40 min ago)
        pos_t2 = context_str.find("Task 'Add Database Indexing' moved to IN_PROGRESS")
        pos_t1 = context_str.find("Task 'Implement OAuth flow' moved to COMPLETED")
        pos_p1 = context_str.find("Project timeline updated")

        record_test(
            "4. Activities are ordered chronologically (newest first)",
            pos_t2 != -1 and pos_t1 != -1 and pos_p1 != -1 and pos_t2 < pos_t1 < pos_p1,
            f"Ordering incorrect: pos_t2={pos_t2}, pos_t1={pos_t1}, pos_p1={pos_p1}"
        )

        # -------------------------------------------------------------
        # TEST 5: Deduplication check
        # -------------------------------------------------------------
        # If an activity exists in both project_id and legacy entity_id lookup, it should appear exactly once
        count_t1_completed = context_str.count("Task 'Implement OAuth flow' moved to COMPLETED")
        record_test(
            "5. No duplicate activity events are introduced",
            count_t1_completed == 1,
            f"Expected count 1, found {count_t1_completed}"
        )

        # -------------------------------------------------------------
        # TEST 6: Bounded history limit (max 30 items)
        # -------------------------------------------------------------
        bulk_ids = []
        for i in range(40):
            b_id = str(uuid.uuid4())
            bulk_ids.append(b_id)
            activity_ids.append(b_id)
            db.table("activity_logs").insert({
                "id": b_id,
                "user_id": pm_id,
                "action": "BULK_TASK_EVENT",
                "entity_type": "task",
                "entity_id": task1_id,
                "project_id": proj1_id,
                "description": f"Bulk activity log entry #{i:02d}",
                "created_at": (now - timedelta(minutes=100 + i)).isoformat()
            }).execute()

        context_large, raw_large = __import__("asyncio").run(_build_project_context(proj1_id))
        activities_in_raw = raw_large.get("activities", [])
        record_test(
            "6. Bounded history limit is respected (max 30 recent activities)",
            len(activities_in_raw) <= 30,
            f"Found {len(activities_in_raw)} items in raw activities"
        )

        # -------------------------------------------------------------
        # TEST 7: Workflow risks section appears exactly once
        # -------------------------------------------------------------
        record_test(
            "7. VERIFIED WORKFLOW RISKS section appears exactly once",
            context_large.count("VERIFIED WORKFLOW RISKS:") == 1 and "Stuck DB Indexing Task" in context_large,
            f"Workflow risks section count={context_large.count('VERIFIED WORKFLOW RISKS:')}"
        )

        # -------------------------------------------------------------
        # TEST 8: /generate endpoint integration test with mocked LLM
        # -------------------------------------------------------------
        mock_summary = {
            "project_overview": "Alpha project with 2 tasks",
            "previous_developer_work": "OAuth flow was completed by Alice",
            "current_work": "Alice working on Database Indexing",
            "pending_work": "None",
            "blocked_overdue_work": "Stuck indexing task",
            "recent_github_activity": "No GitHub repo",
            "known_issues": "Stuck DB Indexing Task",
            "important_context": "Critical project",
            "what_next_developer_should_know": "Review OAuth and complete indexing",
            "recommended_next_steps": "Complete DB indexing task"
        }

        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            res_gen = client.post(f"/api/v1/projects/{proj1_id}/continuity/generate", headers=pm_h)
            record_test("8. /continuity/generate returns 200 and passes prompt containing task activity", res_gen.status_code == 200, res_gen.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            record_test(
                "8b. Task-level activity reaches LLM prompt in /generate",
                "Implement OAuth flow" in prompt_sent and "TASK_COMPLETED" in prompt_sent,
                "Task activity not found in LLM prompt"
            )

        # -------------------------------------------------------------
        # TEST 9: /ask endpoint with conversation memory + task activity
        # -------------------------------------------------------------
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Alice completed the OAuth flow task."
            res_ask = client.post(
                f"/api/v1/projects/{proj1_id}/continuity/ask",
                headers=dev_h,
                json={
                    "question": "What tasks have been completed so far?",
                    "messages": [
                        {"role": "user", "content": "Hi, what is the status of the project?"},
                        {"role": "assistant", "content": "The project is active with 1 completed task."}
                    ]
                }
            )
            record_test("9. /continuity/ask returns 200 with answer", res_ask.status_code == 200 and "answer" in res_ask.json(), res_ask.text)
            
            prompt_sent_ask = mock_llm.call_args[0][0]
            record_test(
                "9b. Ask prompt contains RECENT CONVERSATION and task activity context",
                "RECENT CONVERSATION:" in prompt_sent_ask and "Implement OAuth flow" in prompt_sent_ask,
                "Ask prompt missing conversation memory or task context"
            )

        # -------------------------------------------------------------
        # TEST 10: RBAC enforcement
        # -------------------------------------------------------------
        # Admin can access
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            res_adm = client.post(f"/api/v1/projects/{proj1_id}/continuity/generate", headers=admin_h)
            record_test("10a. Admin can access project continuity (200)", res_adm.status_code == 200, res_adm.text)

        # Assigned Dev can access
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            res_dev = client.post(f"/api/v1/projects/{proj1_id}/continuity/generate", headers=dev_h)
            record_test("10b. Assigned Developer can access project continuity (200)", res_dev.status_code == 200, res_dev.text)

        # Unassigned Dev2 is blocked (403)
        res_dev2 = client.post(f"/api/v1/projects/{proj1_id}/continuity/generate", headers=dev2_h)
        record_test("10c. Unassigned Developer is forbidden (403)", res_dev2.status_code == 403, res_dev2.text)

        # PM2 (manages other project) is blocked from proj1 (403)
        res_pm2 = client.post(f"/api/v1/projects/{proj1_id}/continuity/generate", headers={"Authorization": f"Bearer {get_token(f'pm2_act_{suffix}@test.com')}"})
        record_test("10d. Foreign Project Manager is forbidden (403)", res_pm2.status_code == 403, res_pm2.text)

        # -------------------------------------------------------------
        # TEST 11: Project History API endpoint
        # -------------------------------------------------------------
        res_hist = client.get(f"/api/v1/projects/{proj1_id}/history", headers=pm_h)
        record_test("11. GET /projects/{id}/history returns 200 and includes task activity", res_hist.status_code == 200, res_hist.text)
        
        hist_items = res_hist.json() if res_hist.status_code == 200 else []
        hist_actions = [h.get("action") for h in hist_items]
        record_test(
            "11b. History endpoint contains both project and task actions",
            "PROJECT_UPDATED" in hist_actions and "TASK_COMPLETED" in hist_actions,
            f"Actions found: {hist_actions[:10]}"
        )

    finally:
        cleanup_records("activity_logs", activity_ids)
        cleanup_records("workflow_risks", [risk_id])
        cleanup_records("tasks", [task1_id, task2_id, task_other_proj_id])
        cleanup_records("projects", [proj1_id, proj2_id])
        cleanup_records("users", [admin_id, pm_id, pm2_id, dev_id, dev2_id])

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
