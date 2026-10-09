"""
Test Suite: GitHub Branch Context in AI Continuity.

Tests:
1. Branches are fetched and included in AI context for handover generation.
2. Branches are included in AI context for Ask AI.
3. Branch data appears in raw_context returned by the API.
4. Empty branch list is handled explicitly ("No branches found.").
5. Branch API failure is captured and surfaced ("Branch data unavailable").
6. Branch names are not used to infer task ownership (instruction present).
7. Existing task mappings, risks, and activity context are preserved.
8. RBAC is unchanged — unauthorized user cannot access branch context.
9. No GitHub repository connected — branches section absent, no crash.
10. Branch data is bounded (per_page=30 in API call).
"""
import os
import sys
import uuid
import json
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password
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
    print("AI DevFlow -- GitHub Branch Context in AI Continuity Tests")
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

    pm_id = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    dev_unauth_id = str(uuid.uuid4())

    proj_id = str(uuid.uuid4())
    proj_no_repo_id = str(uuid.uuid4())

    task_id = str(uuid.uuid4())

    try:
        # Seed users
        db.table("users").insert([
            {"id": pm_id, "name": f"PM Branch {suffix}", "email": f"pm_branch_{suffix}@test.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": dev_id, "name": f"Dev Branch {suffix}", "email": f"dev_branch_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
            {"id": dev_unauth_id, "name": f"Dev Unauth {suffix}", "email": f"dev_unauth_{suffix}@test.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
        ]).execute()

        # Seed projects
        db.table("projects").insert([
            {"id": proj_id, "name": f"Branch Test Project {suffix}", "project_manager_id": pm_id, "status": "active", "progress": 30},
            {"id": proj_no_repo_id, "name": f"No Repo Project {suffix}", "project_manager_id": pm_id, "status": "active", "progress": 10},
        ]).execute()

        # Seed task (gives dev_id access to proj_id)
        db.table("tasks").insert([
            {"id": task_id, "title": f"Branch Feature Task {suffix}", "project_id": proj_id, "assigned_to": dev_id, "status": "IN_PROGRESS", "priority": "HIGH"}
        ]).execute()

        # Seed GitHub repository for proj_id
        db.table("project_github_repositories").insert([
            {"project_id": proj_id, "github_repository_id": "999999", "owner": "test-org", "repository_name": "branch-repo", "full_name": "test-org/branch-repo"}
        ]).execute()

        # Auth tokens
        def get_token(email: str) -> str:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            return res.json()["access_token"]

        pm_h = {"Authorization": f"Bearer {get_token(f'pm_branch_{suffix}@test.com')}"}
        dev_h = {"Authorization": f"Bearer {get_token(f'dev_branch_{suffix}@test.com')}"}
        dev_unauth_h = {"Authorization": f"Bearer {get_token(f'dev_unauth_{suffix}@test.com')}"}

        # ---- Mock with branches present ----
        async def mock_fetch_with_branches(url):
            if "branches" in url:
                return [
                    {"name": "main", "commit": {"sha": "abc1234567890"}},
                    {"name": "feature/login", "commit": {"sha": "def4567890123"}},
                    {"name": "bugfix/crash-fix", "commit": {"sha": "ghi7890123456"}},
                ]
            elif "commits" in url:
                return [{"sha": "c111111", "commit": {"message": f"work on {task_id}", "author": {"name": "Alice", "date": "2026"}}}]
            elif "pulls" in url:
                return []
            elif "issues" in url:
                return []
            return []

        # TEST 1: Branches in context for handover (/generate)
        mock_summary = {
            "project_overview": "Branch test project",
            "previous_developer_work": "None",
            "current_work": "Working on feature",
            "pending_work": "None",
            "blocked_overdue_work": "None",
            "recent_github_activity": "3 branches active",
            "known_issues": "None",
            "important_context": "Has branches",
            "what_next_developer_should_know": "Check branches",
            "recommended_next_steps": "Review branches"
        }

        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_with_branches):
            with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = json.dumps(mock_summary)

                res = client.post(f"/api/v1/projects/{proj_id}/continuity/generate", headers=pm_h)
                record_test("1. /generate returns 200 with branch context", res.status_code == 200, res.text)

                prompt_sent = mock_llm.call_args[0][0]
                record_test(
                    "1b. AI prompt contains GITHUB BRANCHES section",
                    "GITHUB BRANCHES" in prompt_sent,
                    "GITHUB BRANCHES section missing from prompt"
                )
                record_test(
                    "1c. Branch names appear in AI prompt",
                    "main" in prompt_sent and "feature/login" in prompt_sent and "bugfix/crash-fix" in prompt_sent,
                    "Branch names not found in prompt"
                )
                record_test(
                    "1d. Branch short SHAs appear in AI prompt",
                    "abc1234" in prompt_sent and "def4567" in prompt_sent,
                    "Branch short SHAs not found in prompt"
                )

        # TEST 2: Branches in context for Ask AI (/ask)
        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_with_branches):
            with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = "There are 3 active branches: main, feature/login, and bugfix/crash-fix."

                res = client.post(
                    f"/api/v1/projects/{proj_id}/continuity/ask",
                    headers=pm_h,
                    json={"question": "What branches exist in the repository?"}
                )
                record_test("2. /ask returns 200 with branch context", res.status_code == 200 and "answer" in res.json(), res.text)

                prompt_sent = mock_llm.call_args[0][0]
                record_test(
                    "2b. /ask prompt contains GITHUB BRANCHES section",
                    "GITHUB BRANCHES" in prompt_sent and "feature/login" in prompt_sent,
                    "/ask prompt missing branch data"
                )

        # TEST 3: raw_context contains branch data
        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_with_branches):
            with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = json.dumps(mock_summary)

                res = client.post(f"/api/v1/projects/{proj_id}/continuity/generate", headers=pm_h)
                body = res.json()
                raw_ctx = body.get("raw_context", {})
                github_raw = raw_ctx.get("github", {})
                record_test(
                    "3. raw_context.github contains branches list",
                    "branches" in github_raw and len(github_raw["branches"]) == 3,
                    f"branches in raw_context: {github_raw.get('branches')}"
                )

        # TEST 4: Empty branch list handled
        async def mock_fetch_no_branches(url):
            if "branches" in url:
                return []
            elif "commits" in url:
                return []
            elif "pulls" in url:
                return []
            elif "issues" in url:
                return []
            return []

        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_no_branches):
            context_str, raw_dict = asyncio.run(_build_project_context(proj_id))
            record_test(
                "4. Empty branches produces 'No branches found' in context",
                "No branches found" in context_str,
                f"Context does not contain 'No branches found'"
            )

        # TEST 5: Branch API failure captured
        from fastapi import HTTPException as _HTTPException

        async def mock_fetch_branch_error(url):
            if "branches" in url:
                raise _HTTPException(status_code=502, detail="GitHub API rate limit exceeded.")
            elif "commits" in url:
                return []
            elif "pulls" in url:
                return []
            elif "issues" in url:
                return []
            return []

        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_branch_error):
            context_str, raw_dict = asyncio.run(_build_project_context(proj_id))
            record_test(
                "5. Branch API failure surfaced as 'Branch data unavailable'",
                "Branch data unavailable" in context_str and "rate limit" in context_str,
                f"Expected error message not in context"
            )
            record_test(
                "5b. raw_dict.github contains branches_error key on failure",
                "branches_error" in raw_dict.get("github", {}),
                "branches_error key missing from raw_dict.github"
            )

        # TEST 6: No task inference instruction present
        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_with_branches):
            context_str, _ = asyncio.run(_build_project_context(proj_id))
            record_test(
                "6. Branch section warns against inferring task ownership",
                "do NOT infer task ownership" in context_str,
                "Missing instruction about not inferring task ownership from branch names"
            )

        # TEST 7: Existing context sections preserved
        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_with_branches):
            context_str, raw_dict = asyncio.run(_build_project_context(proj_id))
            record_test(
                "7a. VERIFIED GITHUB TASK MAPPINGS section still present",
                "VERIFIED GITHUB TASK MAPPINGS:" in context_str,
                "Task mappings section missing"
            )
            record_test(
                "7b. VERIFIED WORKFLOW RISKS section still present",
                "VERIFIED WORKFLOW RISKS:" in context_str,
                "Workflow risks section missing"
            )
            record_test(
                "7c. RECENT ACTIVITY section still present",
                "RECENT ACTIVITY:" in context_str,
                "Recent activity section missing"
            )

        # TEST 8: RBAC — unassigned dev cannot access
        res_unauth = client.post(f"/api/v1/projects/{proj_id}/continuity/generate", headers=dev_unauth_h)
        record_test("8. Unassigned developer is forbidden (403)", res_unauth.status_code == 403, res_unauth.text)

        # TEST 9: No GitHub repo connected — graceful
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_summary)
            res_no_repo = client.post(f"/api/v1/projects/{proj_no_repo_id}/continuity/generate", headers=pm_h)
            record_test("9. No repo project returns 200 without crash", res_no_repo.status_code == 200, res_no_repo.text)

            body = res_no_repo.json()
            raw_ctx = body.get("raw_context", {})
            github_raw = raw_ctx.get("github", {})
            record_test(
                "9b. raw_context.github.branches is empty list when no repo",
                github_raw.get("branches") == [],
                f"branches: {github_raw.get('branches')}"
            )

        # TEST 10: Branch API call uses bounded per_page
        captured_urls = []

        async def mock_fetch_capture_url(url):
            captured_urls.append(url)
            if "branches" in url:
                return [{"name": f"b{i}", "commit": {"sha": f"sha{i:040d}"}} for i in range(30)]
            elif "commits" in url:
                return []
            elif "pulls" in url:
                return []
            elif "issues" in url:
                return []
            return []

        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch_capture_url):
            asyncio.run(_build_project_context(proj_id))
            branch_urls = [u for u in captured_urls if "branches" in u]
            record_test(
                "10. Branch API call uses per_page=30 for bounded results",
                any("per_page=30" in u for u in branch_urls),
                f"Branch URLs: {branch_urls}"
            )

    finally:
        cleanup_records("project_github_repositories", [])
        try:
            db.table("project_github_repositories").delete().eq("project_id", proj_id).execute()
        except Exception:
            pass
        cleanup_records("tasks", [task_id])
        cleanup_records("projects", [proj_id, proj_no_repo_id])
        cleanup_records("users", [pm_id, dev_id, dev_unauth_id])

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
