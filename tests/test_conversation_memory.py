"""
Milestone Ask AI Short-Term Conversation Memory Tests.

Tests:
1. /ask works without conversation history (backward compatibility).
2. /ask accepts conversation history (messages array).
3. Recent messages are included in the AI prompt under RECENT CONVERSATION.
4. Message history is bounded (only latest 10 messages kept).
5. User and assistant roles are properly formatted.
6. Current question is included after conversation history.
7. Verified project context remains present.
8. VERIFIED WORKFLOW RISKS appears exactly once.
9. Anti-hallucination instruction regarding conversation history is present.
10. Integration test: multi-turn pronoun/context resolution scenario.
11. RBAC enforcement on /ask with conversation history.
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
    print("AI DevFlow -- Ask AI Short-Term Conversation Memory Tests")
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
    dev_id = str(uuid.uuid4())
    unassigned_dev_id = str(uuid.uuid4())

    proj_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    risk_id = str(uuid.uuid4())

    try:
        # Seed users
        db.table("users").insert([
            {"id": admin_id, "name": "Admin User", "email": f"admin_mem_{suffix}@test.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
            {"id": pm_id, "name": "PM Memory", "email": f"pm_mem_{suffix}@test.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": dev_id, "name": "John Doe", "email": f"dev_mem_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
            {"id": unassigned_dev_id, "name": "Unassigned Dev", "email": f"unassigned_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
        ]).execute()

        # Seed project
        db.table("projects").insert([
            {"id": proj_id, "name": f"Memory Test Project {suffix}", "project_manager_id": pm_id, "status": "active", "progress": 65},
        ]).execute()

        # Seed task
        db.table("tasks").insert([
            {"id": task_id, "title": "Implement authentication system", "project_id": proj_id, "assigned_to": dev_id, "status": "COMPLETED", "priority": "CRITICAL"}
        ]).execute()

        # Seed risk
        db.table("workflow_risks").insert([
            {
                "id": risk_id,
                "project_id": proj_id,
                "task_id": task_id,
                "risk_type": "REVIEW_DELAY",
                "level": "Medium",
                "title": "Review Delay Risk",
                "description": "Task was pending review",
                "status": "OPEN",
                "is_resolved": False,
                "detected_at": now.isoformat(),
            }
        ]).execute()

        def get_token(email: str) -> str:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            return res.json()["access_token"]

        pm_h = {"Authorization": f"Bearer {get_token(f'pm_mem_{suffix}@test.com')}"}
        dev_h = {"Authorization": f"Bearer {get_token(f'dev_mem_{suffix}@test.com')}"}
        unassigned_h = {"Authorization": f"Bearer {get_token(f'unassigned_{suffix}@test.com')}"}

        # TEST 1: Backward compatibility: /ask without messages field
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "John completed the authentication task."
            res = client.post(
                f"/api/v1/projects/{proj_id}/continuity/ask",
                headers=pm_h,
                json={"question": "Who completed the authentication task?"}
            )
            record_test("1. /ask works with question only (backward compatibility)", res.status_code == 200 and "answer" in res.json(), res.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            record_test("2. /ask prompt has 'RECENT CONVERSATION:\nNone' when no history is passed", "RECENT CONVERSATION:\nNone" in prompt_sent, "History section format incorrect")
            record_test("3. /ask prompt contains the question", "Who completed the authentication task?" in prompt_sent and "QUESTION:" in prompt_sent, "Question missing from prompt")

        # TEST 2: /ask with conversation history (messages array)
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "He completed it yesterday."
            messages = [
                {"role": "user", "content": "Who completed the authentication task?"},
                {"role": "assistant", "content": "John completed it."}
            ]
            res = client.post(
                f"/api/v1/projects/{proj_id}/continuity/ask",
                headers=pm_h,
                json={
                    "question": "When did he complete it?",
                    "messages": messages
                }
            )
            record_test("4. /ask accepts messages conversation history (200)", res.status_code == 200, res.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            record_test(
                "5. Recent conversation history formatted correctly in prompt",
                "User: Who completed the authentication task?" in prompt_sent and "Assistant: John completed it." in prompt_sent,
                "Conversation history not found in prompt"
            )
            record_test(
                "6. Current question is placed after conversation history",
                prompt_sent.find("RECENT CONVERSATION:") < prompt_sent.find("QUESTION:") and "When did he complete it?" in prompt_sent,
                "Question position is incorrect"
            )

        # TEST 3: Multi-turn Follow-up scenario integration test
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Yes, that Review Delay Risk is currently open."
            messages = [
                {"role": "user", "content": "What are the current project risks?"},
                {"role": "assistant", "content": "The project has an open Review Delay Risk."}
            ]
            res = client.post(
                f"/api/v1/projects/{proj_id}/continuity/ask",
                headers=dev_h,
                json={
                    "question": "Is that risk still open?",
                    "messages": messages
                }
            )
            record_test("7. Assigned Developer can use conversation memory (200)", res.status_code == 200, res.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            record_test(
                "8. Verified project context and risks remain present with conversation memory",
                "Implement authentication system" in prompt_sent and "REVIEW_DELAY" in prompt_sent and "John Doe" in prompt_sent,
                "Project context missing from prompt"
            )
            record_test(
                "9. VERIFIED WORKFLOW RISKS section appears exactly once",
                prompt_sent.count("VERIFIED WORKFLOW RISKS:") == 1,
                f"Found {prompt_sent.count('VERIFIED WORKFLOW RISKS:')} occurrences"
            )

        # TEST 4: Bounded memory limit (max 10 messages)
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Latest answer."
            # Send 16 historical messages
            large_history = []
            for i in range(16):
                large_history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"Turn {i} message"})
                
            res = client.post(
                f"/api/v1/projects/{proj_id}/continuity/ask",
                headers=pm_h,
                json={
                    "question": "What is the status now?",
                    "messages": large_history
                }
            )
            record_test("10. Large conversation history request succeeds (200)", res.status_code == 200, res.text)
            
            prompt_sent = mock_llm.call_args[0][0]
            # Earliest turns (Turn 0 to Turn 5) should have been pruned
            record_test(
                "11. History is bounded to latest 10 messages (old turns pruned)",
                "Turn 0 message" not in prompt_sent and "Turn 5 message" not in prompt_sent and "Turn 15 message" in prompt_sent,
                "Old messages beyond limit were not pruned"
            )

        # TEST 5: Anti-hallucination rule regarding conversation history
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "The available project data is insufficient."
            res = client.post(
                f"/api/v1/projects/{proj_id}/continuity/ask",
                headers=pm_h,
                json={"question": "Test anti-hallucination"}
            )
            prompt_sent = mock_llm.call_args[0][0]
            record_test(
                "12. Prompt contains rule that conversation history is contextual only and not verified project data",
                "Conversation history is contextual only and must not be treated as verified project data" in prompt_sent,
                "Conversation history anti-hallucination rule missing"
            )

        # TEST 6: RBAC remains enforced on /ask with conversation history
        res_unauth = client.post(
            f"/api/v1/projects/{proj_id}/continuity/ask",
            headers=unassigned_h,
            json={
                "question": "What did they do?",
                "messages": [{"role": "user", "content": "Previous question"}]
            }
        )
        record_test("13. Unassigned developer is blocked from /ask with 403", res_unauth.status_code == 403, res_unauth.text)

    finally:
        cleanup_records("workflow_risks", [risk_id])
        cleanup_records("tasks", [task_id])
        cleanup_records("projects", [proj_id])
        cleanup_records("users", [admin_id, pm_id, dev_id, unassigned_dev_id])

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
