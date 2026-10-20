"""
Test Suite for GitHub Project Repository Mapping & Disambiguation.

Tests:
1. Two distinct projects have separate repository records.
2. GET /projects/{project_id}/repository retrieves exactly the repository associated with that project.
3. Unconnected project returns 200 with None (null in JSON) and never falls back to another project's repo.
4. Switching queries between projects retrieves strictly isolated project data.
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


def run_github_project_mapping_tests():
    print("\n=======================================================")
    print("AI DevFlow -- GitHub Project Repository Mapping Tests")
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
    healthcare_project_id = str(uuid.uuid4())
    employee_project_id = str(uuid.uuid4())
    unconnected_project_id = str(uuid.uuid4())

    health_repo_id = str(uuid.uuid4())
    employee_repo_id = str(uuid.uuid4())

    try:
        # 1. Seed admin user
        db.table("users").insert([
            {
                "id": admin_id,
                "name": "Mapping Test Admin",
                "email": f"admin_map_{suffix}@example.com",
                "password_hash": hash_password(password),
                "role": "admin",
                "is_active": True,
            }
        ]).execute()

        # 2. Seed projects
        db.table("projects").insert([
            {
                "id": healthcare_project_id,
                "name": f"Healthcare Management Platform {suffix}",
                "project_manager_id": admin_id,
                "status": "active",
            },
            {
                "id": employee_project_id,
                "name": f"Employee Management Portal {suffix}",
                "project_manager_id": admin_id,
                "status": "active",
            },
            {
                "id": unconnected_project_id,
                "name": f"Unconnected Project {suffix}",
                "project_manager_id": admin_id,
                "status": "active",
            },
        ]).execute()

        # 3. Seed repository connections
        db.table("project_github_repositories").insert([
            {
                "id": health_repo_id,
                "project_id": healthcare_project_id,
                "owner": "Healthcare-Org",
                "repository_name": "Healthcare-Platform",
                "full_name": "Healthcare-Org/Healthcare-Platform",
                "html_url": "https://github.com/Healthcare-Org/Healthcare-Platform",
                "default_branch": "main",
                "github_repository_id": "90001",
                "connected_by": admin_id,
            },
            {
                "id": employee_repo_id,
                "project_id": employee_project_id,
                "owner": "Devflow-Intelligent",
                "repository_name": "Employee-Management-Portal",
                "full_name": "Devflow-Intelligent/Employee-Management-Portal",
                "html_url": "https://github.com/Devflow-Intelligent/Employee-Management-Portal",
                "default_branch": "develop",
                "github_repository_id": "90002",
                "connected_by": admin_id,
            },
        ]).execute()

        # Login
        res = client.post("/api/v1/auth/login", json={"email": f"admin_map_{suffix}@example.com", "password": password})
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # TEST 1: Healthcare project returns its exact repository
        res_health = client.get(f"/api/v1/github/projects/{healthcare_project_id}/repository", headers=headers)
        data_health = res_health.json()
        record_test(
            "1. Healthcare project returns exact Healthcare-Org repository",
            res_health.status_code == 200
            and data_health is not None
            and data_health.get("full_name") == "Healthcare-Org/Healthcare-Platform"
            and data_health.get("project_id") == healthcare_project_id,
            f"Status: {res_health.status_code}, Body: {data_health}",
        )

        # TEST 2: Employee portal returns its exact repository
        res_emp = client.get(f"/api/v1/github/projects/{employee_project_id}/repository", headers=headers)
        data_emp = res_emp.json()
        record_test(
            "2. Employee project returns exact Devflow-Intelligent repository",
            res_emp.status_code == 200
            and data_emp is not None
            and data_emp.get("full_name") == "Devflow-Intelligent/Employee-Management-Portal"
            and data_emp.get("project_id") == employee_project_id,
            f"Status: {res_emp.status_code}, Body: {data_emp}",
        )

        # TEST 3: Switching back and forth ensures no cross-talk or caching pollution
        res_health_again = client.get(f"/api/v1/github/projects/{healthcare_project_id}/repository", headers=headers)
        data_health_again = res_health_again.json()
        record_test(
            "3. Subsequent query for Healthcare still returns Healthcare-Org (no cross-project pollution)",
            res_health_again.status_code == 200
            and data_health_again.get("full_name") == "Healthcare-Org/Healthcare-Platform",
            f"Body: {data_health_again}",
        )

        # TEST 4: Project with NO repository returns null (200 None), not another project's repo
        res_unconnected = client.get(f"/api/v1/github/projects/{unconnected_project_id}/repository", headers=headers)
        data_unconnected = res_unconnected.json()
        record_test(
            "4. Unconnected project returns null and never falls back to another project's repo",
            res_unconnected.status_code == 200 and data_unconnected is None,
            f"Status: {res_unconnected.status_code}, Body: {data_unconnected}",
        )

    finally:
        cleanup_db([health_repo_id, employee_repo_id], "project_github_repositories")
        cleanup_db([healthcare_project_id, employee_project_id, unconnected_project_id], "projects")
        cleanup_db([admin_id], "users")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_github_project_mapping_tests()
