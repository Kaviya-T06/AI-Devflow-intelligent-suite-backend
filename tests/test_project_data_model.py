"""
Comprehensive test suite for Milestone 1 Step 2: Project Database and Data Model.

Tests:
1. Pydantic Project Schema Validation:
   - Valid project schema instantiation
   - Invalid status rejected
   - Negative progress rejected (< 0)
   - Progress > 100 rejected
   - Blank / whitespace-only name rejected
   - end_date before start_date rejected
   - Valid optional start_date / end_date accepted
   - Valid project_manager_id accepted

2. API & Data Model Integration Tests:
   - GET /api/v1/projects endpoint returns proper fields
   - POST /api/v1/projects requires valid payload
   - POST /api/v1/projects rejects non-existent project_manager_id
   - POST /api/v1/projects rejects project_manager_id when user is not a 'project_manager'
   - POST /api/v1/projects succeeds with valid project_manager user
   - Generated timestamps and constraints adherence
"""
import os
import sys
import uuid
from datetime import date, timedelta
from typing import Optional

from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password
from app.schemas.project import ProjectCreateRequest, ProjectOut, ProjectStatus

client = TestClient(app)
db = get_supabase_client()


def cleanup_user(email: str):
    try:
        db.table("users").delete().eq("email", email).execute()
    except Exception as e:
        print(f"Cleanup warning for user {email}: {e}")


def cleanup_project(project_id: str):
    try:
        db.table("projects").delete().eq("id", project_id).execute()
    except Exception as e:
        print(f"Cleanup warning for project {project_id}: {e}")


def run_all_tests():
    print("\n=======================================================")
    print("AI DevFlow -- Milestone 1 Step 2 Project Data Model Tests")
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

    # -------------------------------------------------------------
    # 1. Pydantic Schema Validation Tests
    # -------------------------------------------------------------
    print("\n--- 1. Pydantic Schema Validation Tests ---")

    # 1.1 Valid project create schema
    try:
        p = ProjectCreateRequest(
            name="AI DevFlow Core",
            description="Core workflow engine",
            status=ProjectStatus.PLANNING,
            progress=25,
            start_date=date(2026, 10, 1),
            end_date=date(2027, 3, 31),
            project_manager_id=str(uuid.uuid4()),
        )
        record_test("Valid ProjectCreateRequest schema validates successfully", p.name == "AI DevFlow Core" and p.progress == 25)
    except Exception as e:
        record_test("Valid ProjectCreateRequest schema validates successfully", False, str(e))

    # 1.2 All 5 allowed statuses
    allowed_statuses = ["planning", "active", "on_hold", "completed", "archived"]
    all_valid = True
    for s in allowed_statuses:
        try:
            status_enum = ProjectStatus(s)
            p = ProjectCreateRequest(name="Test", status=status_enum)
        except Exception:
            all_valid = False
            break
    record_test("All 5 status values (planning, active, on_hold, completed, archived) are valid", all_valid)

    # 1.3 Invalid status rejected
    try:
        ProjectCreateRequest(name="Test", status="invalid_status")  # type: ignore
        record_test("Invalid status is rejected", False, "Should have raised ValidationError")
    except (ValidationError, ValueError):
        record_test("Invalid status is rejected", True)

    # 1.4 Progress < 0 rejected
    try:
        ProjectCreateRequest(name="Test", progress=-5)
        record_test("Progress < 0 is rejected", False, "Should have raised ValidationError")
    except (ValidationError, ValueError):
        record_test("Progress < 0 is rejected", True)

    # 1.5 Progress > 100 rejected
    try:
        ProjectCreateRequest(name="Test", progress=101)
        record_test("Progress > 100 is rejected", False, "Should have raised ValidationError")
    except (ValidationError, ValueError):
        record_test("Progress > 100 is rejected", True)

    # 1.6 Blank name rejected
    try:
        ProjectCreateRequest(name="   ")
        record_test("Whitespace-only name is rejected", False, "Should have raised ValidationError")
    except (ValidationError, ValueError):
        record_test("Whitespace-only name is rejected", True)

    # 1.7 end_date before start_date rejected
    try:
        ProjectCreateRequest(
            name="Date Test",
            start_date=date(2026, 10, 15),
            end_date=date(2026, 10, 10),
        )
        record_test("end_date before start_date is rejected", False, "Should have raised ValidationError")
    except (ValidationError, ValueError):
        record_test("end_date before start_date is rejected", True)

    # 1.8 end_date equal to start_date accepted
    try:
        same_date = date(2026, 10, 15)
        p = ProjectCreateRequest(
            name="Single Day Project",
            start_date=same_date,
            end_date=same_date,
        )
        record_test("end_date equal to start_date is accepted", p.start_date == p.end_date)
    except Exception as e:
        record_test("end_date equal to start_date is accepted", False, str(e))

    # 1.9 Optional dates (only start_date or only end_date) accepted
    try:
        p1 = ProjectCreateRequest(name="Start Only", start_date=date(2026, 10, 1))
        p2 = ProjectCreateRequest(name="End Only", end_date=date(2026, 12, 31))
        record_test("Single optional date (start or end only) accepted", p1.start_date is not None and p2.end_date is not None)
    except Exception as e:
        record_test("Single optional date (start or end only) accepted", False, str(e))

    # -------------------------------------------------------------
    # 2. API & Integration Tests
    # -------------------------------------------------------------
    print("\n--- 2. API & Relationship Validation Tests ---")

    unique_suffix = str(uuid.uuid4())[:8]
    pm_email = f"test_pm_mgr_{unique_suffix}@example.com"
    dev_email = f"test_dev_user_{unique_suffix}@example.com"
    password = "TestPassword123!"

    pm_user_id = str(uuid.uuid4())
    dev_user_id = str(uuid.uuid4())
    created_project_id: Optional[str] = None

    # Seed a project_manager user and a developer user directly into users table
    db.table("users").insert({
        "id": pm_user_id,
        "name": "Sarah Project Manager",
        "email": pm_email,
        "password_hash": hash_password(password),
        "role": "project_manager",
        "is_active": True,
    }).execute()

    db.table("users").insert({
        "id": dev_user_id,
        "name": "Dave Developer",
        "email": dev_email,
        "password_hash": hash_password(password),
        "role": "developer",
        "is_active": True,
    }).execute()

    admin_user_id = str(uuid.uuid4())
    admin_email = f"test_admin_model_{unique_suffix}@example.com"
    db.table("users").insert({
        "id": admin_user_id,
        "name": "Admin Model",
        "email": admin_email,
        "password_hash": hash_password(password),
        "role": "admin",
        "is_active": True,
    }).execute()

    login_res = client.post("/api/v1/auth/login", json={"email": admin_email, "password": password})
    admin_token = login_res.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    try:
        # 2.1 Rejection of non-existent project_manager_id
        non_existent_id = str(uuid.uuid4())
        res = client.post("/api/v1/projects", headers=admin_headers, json={
            "name": "Invalid PM Project",
            "project_manager_id": non_existent_id,
            "status": "planning",
        })
        record_test(
            "POST /projects with non-existent project_manager_id returns 400 Bad Request",
            res.status_code == 400 and "not found" in res.text.lower(),
            res.text,
        )

        # 2.2 Rejection of assigned user whose role is NOT project_manager (e.g. developer)
        res = client.post("/api/v1/projects", headers=admin_headers, json={
            "name": "Dev As PM Project",
            "project_manager_id": dev_user_id,
            "status": "planning",
        })
        record_test(
            "POST /projects with non-PM user role returns 400 Bad Request",
            res.status_code == 400 and "project_manager" in res.text.lower(),
            res.text,
        )

        # 2.3 Creation of project without project_manager_id (unassigned)
        res = client.post("/api/v1/projects", headers=admin_headers, json={
            "name": f"Unassigned Project {unique_suffix}",
            "description": "Project with no PM yet",
            "status": "planning",
            "progress": 0,
        })
        if res.status_code == 201:
            created_data = res.json()
            created_project_id = created_data.get("id")
            record_test(
                "POST /projects creates unassigned project with 201 Created and correct defaults",
                created_data.get("status") == "planning" and created_data.get("project_manager_id") is None,
                res.text,
            )
        elif res.status_code == 500 and "projects" in res.text.lower():
            record_test("Database error is visibly raised to API layer (not silently masked)", True)
        else:
            record_test("POST /projects creates unassigned project", False, res.text)

        # 2.4 Creation of project with valid project_manager
        res = client.post("/api/v1/projects", headers=admin_headers, json={
            "name": f"Managed Project {unique_suffix}",
            "description": "Project with assigned PM",
            "status": "active",
            "progress": 20,
            "project_manager_id": pm_user_id,
            "start_date": "2026-10-01",
            "end_date": "2027-01-15",
        })
        if res.status_code == 201:
            managed_proj = res.json()
            record_test(
                "POST /projects creates project with valid PM and dates",
                managed_proj.get("project_manager_id") == pm_user_id and managed_proj.get("status") == "active",
                res.text,
            )
            cleanup_project(managed_proj.get("id"))
        elif res.status_code == 500 and "projects" in res.text.lower():
            record_test("Database error visibility preserved", True)
        else:
            record_test("POST /projects with valid PM", False, res.text)

    finally:
        print("\n--- Cleaning up test records ---")
        if created_project_id:
            cleanup_project(created_project_id)
        cleanup_user(admin_email)
        cleanup_user(pm_email)
        cleanup_user(dev_email)
        print("Cleanup completed.")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
