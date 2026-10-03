"""
Test Suite for GitHub Integration RBAC (Role-Based Access Control).

Tests:
1. Admin can access GitHub data for authorized projects.
2. Project Manager can access GitHub data for their managed project.
3. Developer can access GitHub data for an authorized project.
4. Developer cannot access GitHub data for another developer's/unrelated project.
5. Invalid/nonexistent project remains protected.
6. Authentication is still required.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password

client = TestClient(app)
db = get_supabase_client()


def cleanup_db(ids_to_delete, table_name):
    for record_id in ids_to_delete:
        try:
            db.table(table_name).delete().eq("id", record_id).execute()
        except Exception as e:
            print(f"Cleanup warning for {table_name} {record_id}: {e}")

def run_github_rbac_tests():
    print("\n=======================================================")
    print("AI DevFlow -- GitHub Integration RBAC Tests")
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

    admin_id = str(uuid.uuid4())
    pm_id = str(uuid.uuid4())
    pm2_id = str(uuid.uuid4())
    dev1_id = str(uuid.uuid4())
    dev2_id = str(uuid.uuid4())
    
    project1_id = str(uuid.uuid4())
    project2_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    try:
        # Seed users
        db.table("users").insert([
            {"id": admin_id, "name": "Admin", "email": f"admin_{suffix}@example.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
            {"id": pm_id, "name": "PM", "email": f"pm_{suffix}@example.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": pm2_id, "name": "PM2", "email": f"pm2_{suffix}@example.com", "password_hash": hash_password(password), "role": "project_manager", "is_active": True},
            {"id": dev1_id, "name": "Dev1", "email": f"dev1_{suffix}@example.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
            {"id": dev2_id, "name": "Dev2", "email": f"dev2_{suffix}@example.com", "password_hash": hash_password(password), "role": "developer", "is_active": True},
        ]).execute()

        # Seed projects
        db.table("projects").insert([
            {"id": project1_id, "name": "Project 1", "project_manager_id": pm_id, "status": "active"},
            {"id": project2_id, "name": "Project 2", "project_manager_id": pm2_id, "status": "active"},
        ]).execute()

        # Seed tasks (giving dev1 access to project 1)
        db.table("tasks").insert([
            {"id": task_id, "title": "Task 1", "project_id": project1_id, "assigned_to": dev1_id, "status": "todo", "priority": "high", "description": "desc"}
        ]).execute()

        def get_token(email: str) -> str:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            return res.json()["access_token"]

        admin_token = get_token(f"admin_{suffix}@example.com")
        pm_token = get_token(f"pm_{suffix}@example.com")
        pm2_token = get_token(f"pm2_{suffix}@example.com")
        dev1_token = get_token(f"dev1_{suffix}@example.com")
        dev2_token = get_token(f"dev2_{suffix}@example.com")

        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        pm_headers = {"Authorization": f"Bearer {pm_token}"}
        pm2_headers = {"Authorization": f"Bearer {pm2_token}"}
        dev1_headers = {"Authorization": f"Bearer {dev1_token}"}
        dev2_headers = {"Authorization": f"Bearer {dev2_token}"}

        # TEST 1: Admin can access GitHub data
        res = client.get(f"/api/v1/github/projects/{project1_id}/repository", headers=admin_headers)
        record_test("1. Admin can access GitHub data for authorized projects (returns 200)", res.status_code == 200, res.text)

        # TEST 2: PM can access their own project's GitHub data
        res = client.get(f"/api/v1/github/projects/{project1_id}/repository", headers=pm_headers)
        record_test("2. PM can access GitHub data for managed project", res.status_code == 200, res.text)
        
        # TEST 2b: PM cannot access another PM's project
        res = client.get(f"/api/v1/github/projects/{project2_id}/repository", headers=pm_headers)
        record_test("2b. PM cannot access GitHub data for another PM's project (returns 403)", res.status_code == 403, res.text)

        # TEST 3: Developer 1 can access project 1 (has a task assigned)
        res = client.get(f"/api/v1/github/projects/{project1_id}/repository", headers=dev1_headers)
        record_test("3. Developer can access GitHub data for authorized project (returns 200)", res.status_code == 200, res.text)

        # TEST 4: Developer 2 cannot access project 1 (no task assigned)
        res = client.get(f"/api/v1/github/projects/{project1_id}/repository", headers=dev2_headers)
        record_test("4. Developer cannot access GitHub data for unrelated project (returns 403)", res.status_code == 403, res.text)

        # TEST 5: Invalid/nonexistent project ID
        fake_id = str(uuid.uuid4())
        res = client.get(f"/api/v1/github/projects/{fake_id}/repository", headers=admin_headers)
        record_test("5. Invalid/nonexistent project returns 404", res.status_code == 404, res.text)

        # TEST 6: Authentication is required
        res = client.get(f"/api/v1/github/projects/{project1_id}/repository")
        record_test("6. Authentication required (returns 401)", res.status_code == 401, res.text)

    finally:
        cleanup_db([task_id], "tasks")
        cleanup_db([project1_id, project2_id], "projects")
        cleanup_db([admin_id, pm_id, pm2_id, dev1_id, dev2_id], "users")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_github_rbac_tests()
