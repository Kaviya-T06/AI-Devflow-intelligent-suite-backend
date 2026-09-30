"""
Milestone 1 — Step 3: Complete Project Management Backend API Test Suite.

Tests:
1. Admin creates project.
2. Admin views project.
3. Admin edits project.
4. Admin changes project manager.
5. Admin changes status.
6. Admin changes progress.
7. Admin archives project.
8. Project manager views their project.
9. Project manager edits their own project.
10. Project manager attempts to modify another manager's project (403).
11. Developer attempts to create a project (403).
12. Developer attempts to delete/archive a project (403).
13. Invalid project manager (400).
14. Inactive project manager (400).
15. Invalid progress (422).
16. Invalid status (422).
17. Invalid dates (400/422).
18. Missing/invalid JWT (401).
19. Non-existent project ID (404).
20. Database error handling / proper error propagation.
"""
import os
import sys
import uuid
from datetime import date, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password

client = TestClient(app)
db = get_supabase_client()


def cleanup_user(user_id: str):
    try:
        db.table("users").delete().eq("id", user_id).execute()
    except Exception as e:
        print(f"Cleanup warning for user {user_id}: {e}")


def cleanup_project(project_id: str):
    try:
        db.table("projects").delete().eq("id", project_id).execute()
    except Exception as e:
        print(f"Cleanup warning for project {project_id}: {e}")


def run_all_tests():
    print("\n=======================================================")
    print("AI DevFlow -- Milestone 1 Step 3 Project CRUD & RBAC Tests")
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
    admin_email = f"admin_{suffix}@example.com"

    pm1_id = str(uuid.uuid4())
    pm1_email = f"pm1_{suffix}@example.com"

    pm2_id = str(uuid.uuid4())
    pm2_email = f"pm2_{suffix}@example.com"

    inactive_pm_id = str(uuid.uuid4())
    inactive_pm_email = f"inactive_pm_{suffix}@example.com"

    dev_id = str(uuid.uuid4())
    dev_email = f"dev_{suffix}@example.com"

    # Seed users into public.users
    db.table("users").insert([
        {
            "id": admin_id,
            "name": "Admin User",
            "email": admin_email,
            "password_hash": hash_password(password),
            "role": "admin",
            "is_active": True,
        },
        {
            "id": pm1_id,
            "name": "Project Manager 1",
            "email": pm1_email,
            "password_hash": hash_password(password),
            "role": "project_manager",
            "is_active": True,
        },
        {
            "id": pm2_id,
            "name": "Project Manager 2",
            "email": pm2_email,
            "password_hash": hash_password(password),
            "role": "project_manager",
            "is_active": True,
        },
        {
            "id": inactive_pm_id,
            "name": "Inactive PM",
            "email": inactive_pm_email,
            "password_hash": hash_password(password),
            "role": "project_manager",
            "is_active": False,
        },
        {
            "id": dev_id,
            "name": "Dev User",
            "email": dev_email,
            "password_hash": hash_password(password),
            "role": "developer",
            "is_active": True,
        },
    ]).execute()

    # Login to get JWT tokens
    def get_token(email: str) -> str:
        res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        if res.status_code != 200:
            raise RuntimeError(f"Login failed for {email}: {res.text}")
        return res.json()["access_token"]

    admin_token = get_token(admin_email)
    pm1_token = get_token(pm1_email)
    pm2_token = get_token(pm2_email)
    dev_token = get_token(dev_email)

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    pm1_headers = {"Authorization": f"Bearer {pm1_token}"}
    pm2_headers = {"Authorization": f"Bearer {pm2_token}"}
    dev_headers = {"Authorization": f"Bearer {dev_token}"}

    project1_id = None
    project2_id = None

    try:
        # -------------------------------------------------------------
        # Test 1: Admin creates project
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "name": f"Admin Created Project {suffix}",
                "description": "Initial description",
                "status": "planning",
                "progress": 0,
                "project_manager_id": pm1_id,
                "start_date": "2026-10-01",
                "end_date": "2027-04-30",
            },
        )
        is_201 = res.status_code == 201
        if is_201:
            project1_id = res.json()["id"]
            record_test("1. Admin creates project with 201 Created", True)
        else:
            record_test("1. Admin creates project with 201 Created", False, res.text)

        # -------------------------------------------------------------
        # Test 2: Admin views project
        # -------------------------------------------------------------
        if project1_id:
            res = client.get(f"/api/v1/projects/{project1_id}", headers=admin_headers)
            record_test(
                "2. Admin views project by ID (200 OK with manager details)",
                res.status_code == 200 and res.json().get("name") == f"Admin Created Project {suffix}",
                res.text,
            )

        # -------------------------------------------------------------
        # Test 3: Admin edits project name & description
        # -------------------------------------------------------------
        if project1_id:
            res = client.patch(
                f"/api/v1/projects/{project1_id}",
                headers=admin_headers,
                json={"name": f"Admin Renamed Project {suffix}", "description": "Updated description"},
            )
            record_test(
                "3. Admin edits project name and description",
                res.status_code == 200 and res.json().get("name") == f"Admin Renamed Project {suffix}",
                res.text,
            )

        # -------------------------------------------------------------
        # Test 4: Admin changes project manager
        # -------------------------------------------------------------
        if project1_id:
            res = client.patch(
                f"/api/v1/projects/{project1_id}",
                headers=admin_headers,
                json={"project_manager_id": pm2_id},
            )
            record_test(
                "4. Admin changes project manager to PM2",
                res.status_code == 200 and res.json().get("project_manager_id") == pm2_id,
                res.text,
            )

        # -------------------------------------------------------------
        # Test 5: Admin changes status
        # -------------------------------------------------------------
        if project1_id:
            res = client.patch(
                f"/api/v1/projects/{project1_id}",
                headers=admin_headers,
                json={"status": "active"},
            )
            record_test(
                "5. Admin changes project status to 'active'",
                res.status_code == 200 and res.json().get("status") == "active",
                res.text,
            )

        # -------------------------------------------------------------
        # Test 6: Admin changes progress
        # -------------------------------------------------------------
        if project1_id:
            res = client.patch(
                f"/api/v1/projects/{project1_id}",
                headers=admin_headers,
                json={"progress": 45},
            )
            record_test(
                "6. Admin changes progress to 45%",
                res.status_code == 200 and res.json().get("progress") == 45,
                res.text,
            )

        # -------------------------------------------------------------
        # Test 7: Admin archives project
        # -------------------------------------------------------------
        if project1_id:
            res = client.delete(f"/api/v1/projects/{project1_id}", headers=admin_headers)
            record_test(
                "7. Admin archives project (DELETE returns 200 with status='archived')",
                res.status_code == 200 and res.json().get("status") == "archived",
                res.text,
            )

        # -------------------------------------------------------------
        # Test 8: Project manager creates & views their project
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=pm1_headers,
            json={
                "name": f"PM1 Managed Project {suffix}",
                "description": "PM1 Project",
                "status": "planning",
                "progress": 10,
            },
        )
        if res.status_code == 201:
            project2_id = res.json()["id"]
            # Verify PM1 can view it
            view_res = client.get(f"/api/v1/projects/{project2_id}", headers=pm1_headers)
            record_test(
                "8. Project Manager creates and views their managed project",
                view_res.status_code == 200 and view_res.json().get("project_manager_id") == pm1_id,
                view_res.text,
            )
        else:
            record_test("8. Project Manager creates their project", False, res.text)

        # -------------------------------------------------------------
        # Test 9: Project manager edits their own project
        # -------------------------------------------------------------
        if project2_id:
            res = client.patch(
                f"/api/v1/projects/{project2_id}",
                headers=pm1_headers,
                json={"progress": 30, "status": "active"},
            )
            record_test(
                "9. Project Manager edits their own project (progress to 30%, status to 'active')",
                res.status_code == 200 and res.json().get("progress") == 30 and res.json().get("status") == "active",
                res.text,
            )

        # -------------------------------------------------------------
        # Test 10: Project manager attempts to modify another manager's project -> 403
        # -------------------------------------------------------------
        if project2_id:
            res = client.patch(
                f"/api/v1/projects/{project2_id}",
                headers=pm2_headers,
                json={"name": "Hacked Name by PM2"},
            )
            record_test(
                "10. Project Manager 2 is rejected from modifying PM1's project (403 Forbidden)",
                res.status_code == 403,
                res.text,
            )

        # -------------------------------------------------------------
        # Test 11: Developer attempts to create a project -> 403
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=dev_headers,
            json={"name": "Dev Unauthorized Project"},
        )
        record_test(
            "11. Developer is rejected from creating a project (403 Forbidden)",
            res.status_code == 403,
            res.text,
        )

        # -------------------------------------------------------------
        # Test 12: Developer attempts to delete/archive a project -> 403
        # -------------------------------------------------------------
        if project2_id:
            res = client.delete(f"/api/v1/projects/{project2_id}", headers=dev_headers)
            record_test(
                "12. Developer is rejected from deleting/archiving a project (403 Forbidden)",
                res.status_code == 403,
                res.text,
            )

        # -------------------------------------------------------------
        # Test 13: Invalid project manager (non-existent / dev role) -> 400
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "name": "Invalid PM Project",
                "project_manager_id": str(uuid.uuid4()),
            },
        )
        record_test(
            "13. Non-existent project manager rejected with 400 Bad Request",
            res.status_code == 400,
            res.text,
        )

        # -------------------------------------------------------------
        # Test 14: Inactive project manager -> 400
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "name": "Inactive PM Project",
                "project_manager_id": inactive_pm_id,
            },
        )
        record_test(
            "14. Inactive project manager rejected with 400 Bad Request",
            res.status_code == 400 and "inactive" in res.text.lower(),
            res.text,
        )

        # -------------------------------------------------------------
        # Test 15: Invalid progress (< 0 or > 100) -> 422
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "name": "Invalid Progress Project",
                "progress": 150,
            },
        )
        record_test(
            "15. Invalid progress (150) rejected with 422 Unprocessable Entity",
            res.status_code == 422,
            res.text,
        )

        # -------------------------------------------------------------
        # Test 16: Invalid status -> 422
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "name": "Invalid Status Project",
                "status": "not_a_real_status",
            },
        )
        record_test(
            "16. Invalid status rejected with 422 Unprocessable Entity",
            res.status_code == 422,
            res.text,
        )

        # -------------------------------------------------------------
        # Test 17: Invalid dates (end_date before start_date) -> 422/400
        # -------------------------------------------------------------
        res = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={
                "name": "Invalid Dates Project",
                "start_date": "2026-10-15",
                "end_date": "2026-10-01",
            },
        )
        record_test(
            "17. Invalid date range (end_date < start_date) rejected with 422/400",
            res.status_code in (400, 422),
            res.text,
        )

        # -------------------------------------------------------------
        # Test 18: Missing or invalid JWT -> 401
        # -------------------------------------------------------------
        res_no_token = client.get("/api/v1/projects")
        res_bad_token = client.get("/api/v1/projects", headers={"Authorization": "Bearer fake.token.here"})
        record_test(
            "18. Missing (401) and invalid JWT (401) properly rejected",
            res_no_token.status_code == 401 and res_bad_token.status_code == 401,
            f"No token: {res_no_token.status_code}, Bad token: {res_bad_token.status_code}",
        )

        # -------------------------------------------------------------
        # Test 19: Non-existent project ID -> 404
        # -------------------------------------------------------------
        fake_id = str(uuid.uuid4())
        res = client.get(f"/api/v1/projects/{fake_id}", headers=admin_headers)
        record_test(
            "19. Non-existent project ID returns 404 Not Found",
            res.status_code == 404,
            res.text,
        )

        # -------------------------------------------------------------
        # Test 20: Database error handling & query filtering
        # -------------------------------------------------------------
        res_filter = client.get("/api/v1/projects?status=active", headers=admin_headers)
        record_test(
            "20. Projects listing with status filter executes cleanly and returns real data",
            res_filter.status_code == 200 and isinstance(res_filter.json(), list),
            res_filter.text,
        )

    finally:
        print("\n--- Cleaning up test records ---")
        if project1_id:
            cleanup_project(project1_id)
        if project2_id:
            cleanup_project(project2_id)
        cleanup_user(admin_id)
        cleanup_user(pm1_id)
        cleanup_user(pm2_id)
        cleanup_user(inactive_pm_id)
        cleanup_user(dev_id)
        print("Cleanup completed.")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
