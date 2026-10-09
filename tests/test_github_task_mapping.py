import os
import sys
import uuid
import asyncio
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.db.supabase_client import get_supabase_client
from app.services.continuity_service import _build_project_context

db = get_supabase_client()

def run_tests():
    print("\n=======================================================")
    print("AI DevFlow — Milestone 5: GitHub Task Mapping Tests")
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

    # Seed data
    proj_id = str(uuid.uuid4())
    proj2_id = str(uuid.uuid4())
    task1_id = str(uuid.uuid4())
    task2_id = str(uuid.uuid4())
    task_other_proj_id = str(uuid.uuid4())
    
    db.table("projects").insert([
        {"id": proj_id, "name": "Mapping Test Proj", "status": "active"},
        {"id": proj2_id, "name": "Other Proj", "status": "active"},
    ]).execute()
    
    db.table("project_github_repositories").insert([
        {"project_id": proj_id, "github_repository_id": "123456", "owner": "test", "repository_name": "repo", "full_name": "test/repo"}
    ]).execute()

    db.table("tasks").insert([
        {"id": task1_id, "project_id": proj_id, "title": "Implement feature X"},
        {"id": task2_id, "project_id": proj_id, "title": "Fix bug Y"},
        {"id": task_other_proj_id, "project_id": proj2_id, "title": "Other task"},
    ]).execute()

    # Mock github responses
    async def mock_fetch(url):
        if "commits" in url:
            return [
                {"sha": "c111111", "commit": {"message": f"Fixed bug {task1_id}", "author": {"name": "Alice", "date": "2026"}}},
                {"sha": "c222222", "commit": {"message": f"Did something without task ID", "author": {"name": "Bob", "date": "2026"}}},
                {"sha": "c333333", "commit": {"message": f"Referencing task from other proj {task_other_proj_id}", "author": {"name": "Charlie", "date": "2026"}}},
                {"sha": "c444444", "commit": {"message": f"Multiple references {task1_id} and {task1_id} again", "author": {"name": "Dave", "date": "2026"}}},
                {"sha": "c555555", "commit": {"message": f"Malformed UUID 12345678-1234-1234-1234-12345678901z", "author": {"name": "Eve", "date": "2026"}}}
            ]
        elif "pulls" in url:
            return [
                {"title": f"Feature PR {task2_id}", "body": "This implements the feature", "state": "open", "user": {"login": "Alice"}},
                {"title": "Unmapped PR", "body": "No ID here", "state": "closed", "user": {"login": "Bob"}}
            ]
        elif "issues" in url:
            return []
        return []

    try:
        with patch("app.services.continuity_service.fetch_github_api", side_effect=mock_fetch):
            context, _ = asyncio.run(_build_project_context(proj_id))
            
            # Test 1: Valid Task ID in commit message
            record_test("1. Valid Task ID in commit message -> mapped", 
                        f"Commit c111111 -> DevFlow Task: {task1_id}" in context)
            
            # Test 2: Valid Task ID in PR title/body -> mapped
            record_test("2. Valid Task ID in PR title/body -> mapped", 
                        f"Pull Request [open] 'Feature PR {task2_id}' -> DevFlow Task: {task2_id}" in context)
            
            # Test 3: Task does not exist -> no mapping
            fake_id = str(uuid.uuid4())
            record_test("3. Task does not exist -> no mapping", 
                        fake_id not in context)
            
            # Test 4: Task exists but belongs to another project -> no mapping
            record_test("4. Task exists but belongs to another project -> no mapping", 
                        f"DevFlow Task: {task_other_proj_id}" not in context)
            
            # Test 5: Multiple GitHub activities reference same task -> handled without duplicate mappings
            # "Multiple references" commit maps to task1_id, but it only maps once for that commit
            record_test("5. Multiple references in one commit map cleanly", 
                        context.count(f"Commit c444444 -> DevFlow Task: {task1_id}") == 1)
            
            # Test 6: No Task ID -> remains unmapped
            record_test("6. No Task ID -> remains unmapped", 
                        "Did something without task ID" in context and "Commit c222222 -> DevFlow Task" not in context)
            
            # Test 7: Invalid/malformed Task ID -> remains unmapped
            record_test("7. Invalid/malformed Task ID -> remains unmapped", 
                        "Malformed UUID" in context and "12345678-1234-1234-1234-12345678901z" not in context.split("VERIFIED GITHUB TASK MAPPINGS:")[1].split("UNMAPPED GITHUB ACTIVITY")[0])
            
            # Test 8: AI context contains verified mappings
            record_test("8. AI context contains VERIFIED GITHUB TASK MAPPINGS section", 
                        "VERIFIED GITHUB TASK MAPPINGS:" in context)
                        
            # Test 9: AI context keeps unmapped GitHub activity separate
            record_test("9. AI context keeps UNMAPPED GITHUB ACTIVITY separate", 
                        "UNMAPPED GITHUB ACTIVITY" in context)

    finally:
        # Cleanup
        db.table("tasks").delete().in_("id", [task1_id, task2_id, task_other_proj_id]).execute()
        db.table("project_github_repositories").delete().eq("project_id", proj_id).execute()
        db.table("projects").delete().in_("id", [proj_id, proj2_id]).execute()

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
