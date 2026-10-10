"""
Comprehensive test suite for Milestone 1 Step 1: Authentication and Role Foundation.

Tests:
- Registration (default developer role, admin escalation prevention, duplicate handling)
- Login (valid, invalid password, non-existent user)
- JWT Authentication (valid token, missing token, invalid/malformed token)
- Role-Based Access Control (Admin, Project Manager, Developer)
- Self profile management (/users/me GET & PATCH)
"""
import uuid
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password
from app.api.deps import require_admin_or_manager
from app.schemas.user import UserOut
from fastapi import APIRouter, Depends

# Create a test route guarded by require_admin_or_manager to explicitly verify the dependency
test_router = APIRouter(prefix="/test-rbac", tags=["Test RBAC"])

@test_router.get("/admin-or-manager")
async def dummy_admin_or_manager_endpoint(
    current_user: UserOut = Depends(require_admin_or_manager)
):
    return {"message": f"Welcome {current_user.name}", "role": current_user.role}

app.include_router(test_router)

client = TestClient(app)
db = get_supabase_client()


def cleanup_test_user(email: str):
    try:
        db.table("users").delete().eq("email", email).execute()
    except Exception as e:
        print(f"Cleanup warning for {email}: {e}")


def run_all_tests():
    print("\n=======================================================")
    print("AI DevFlow -- Milestone 1 Step 1 Auth & Role Test Suite")
    print("=======================================================\n")
    
    unique_suffix = str(uuid.uuid4())[:8]
    dev_email = f"test_dev_{unique_suffix}@example.com"
    escalation_email = f"test_hacker_{unique_suffix}@example.com"
    admin_email = f"test_admin_{unique_suffix}@example.com"
    admin_created_pm_email = f"test_pm_{unique_suffix}@example.com"
    password = "SecurePassword123!"

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

    # Seed a known admin user directly in DB for testing admin actions
    admin_id = str(uuid.uuid4())
    db.table("users").insert({
        "id": admin_id,
        "name": "Test Admin",
        "email": admin_email,
        "password_hash": hash_password(password),
        "role": "admin",
        "is_active": True,
    }).execute()

    try:
        # -------------------------------------------------------------
        # 1. Registration Tests
        # -------------------------------------------------------------
        print("\n--- 1. Registration Tests ---")
        
        # Test 1.1: Standard user registration receives 'developer' role
        res = client.post("/api/v1/auth/register", json={
            "name": "Test Developer",
            "email": dev_email,
            "password": password,
        })
        is_201 = res.status_code == 201
        role_is_dev = res.json().get("user", {}).get("role") == "developer" if is_201 else False
        record_test("Standard user registers with default 'developer' role", is_201 and role_is_dev, res.text)

        # Test 1.2: Client attempts to self-escalate to 'admin' during public registration
        res = client.post("/api/v1/auth/register", json={
            "name": "Hacker Trying Admin",
            "email": escalation_email,
            "password": password,
            "role": "admin"
        })
        is_201 = res.status_code == 201
        assigned_role = res.json().get("user", {}).get("role") if is_201 else None
        escalation_prevented = (assigned_role == "developer")
        record_test("Public registration overrides client 'admin' request to safe 'developer'", is_201 and escalation_prevented, f"Assigned role was: {assigned_role}")

        # Test 1.3: Duplicate email registration returns 409 Conflict
        res = client.post("/api/v1/auth/register", json={
            "name": "Duplicate User",
            "email": dev_email,
            "password": password,
        })
        record_test("Duplicate email returns 409 Conflict", res.status_code == 409, res.text)

        # -------------------------------------------------------------
        # 2. Login Tests
        # -------------------------------------------------------------
        print("\n--- 2. Login Tests ---")

        # Test 2.1: Valid login returns JWT token and user info
        res = client.post("/api/v1/auth/login", json={
            "email": dev_email,
            "password": password,
        })
        dev_token = res.json().get("access_token")
        record_test("Valid login returns JWT access token and 200 OK", res.status_code == 200 and bool(dev_token), res.text)

        # Test 2.2: Invalid password returns 401 Unauthorized
        res = client.post("/api/v1/auth/login", json={
            "email": dev_email,
            "password": "WrongPassword999!",
        })
        record_test("Invalid password returns 401 Unauthorized", res.status_code == 401, res.text)

        # Test 2.3: Non-existent email returns 401 Unauthorized
        res = client.post("/api/v1/auth/login", json={
            "email": "non_existent_user_99999@example.com",
            "password": password,
        })
        record_test("Non-existent email returns 401 Unauthorized", res.status_code == 401, res.text)

        # -------------------------------------------------------------
        # 3. JWT Authentication & Protected Endpoints
        # -------------------------------------------------------------
        print("\n--- 3. JWT Authentication & Token Validation Tests ---")

        # Test 3.1: Protected endpoint GET /users/me with valid token
        res = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {dev_token}"})
        record_test("GET /users/me returns authenticated user with valid JWT", res.status_code == 200 and res.json().get("email") == dev_email, res.text)

        # Test 3.2: Protected endpoint without token returns 401
        res = client.get("/api/v1/users/me")
        record_test("GET /users/me without token returns 401 Unauthorized", res.status_code == 401, res.text)

        # Test 3.3: Protected endpoint with invalid / malformed token returns 401
        res = client.get("/api/v1/users/me", headers={"Authorization": "Bearer invalid.malformed.jwt.token"})
        record_test("GET /users/me with invalid token returns 401 Unauthorized", res.status_code == 401, res.text)

        # -------------------------------------------------------------
        # 4. Role-Based Access Control (RBAC) Tests
        # -------------------------------------------------------------
        print("\n--- 4. Role-Based Access Control (RBAC) Tests ---")

        # Login seeded admin user
        admin_login_res = client.post("/api/v1/auth/login", json={
            "email": admin_email,
            "password": password,
        })
        admin_token = admin_login_res.json().get("access_token")

        # Test 4.1: Admin can create a Project Manager via POST /users (Admin-only)
        res = client.post("/api/v1/users", headers={"Authorization": f"Bearer {admin_token}"}, json={
            "name": "Test Project Manager",
            "email": admin_created_pm_email,
            "password": password,
            "role": "project_manager",
            "is_active": True,
        })
        record_test("Admin can create Project Manager via POST /users", res.status_code == 201 and res.json().get("role") == "project_manager", res.text)

        # Login as the new Project Manager
        pm_login_res = client.post("/api/v1/auth/login", json={
            "email": admin_created_pm_email,
            "password": password,
        })
        pm_token = pm_login_res.json().get("access_token")

        # Test 4.2: Developer attempts Admin-only action (POST /users) -> 403 Forbidden
        res = client.post("/api/v1/users", headers={"Authorization": f"Bearer {dev_token}"}, json={
            "name": "Should Fail",
            "email": "fail@example.com",
            "password": password,
            "role": "developer"
        })
        record_test("Developer is rejected from Admin-only POST /users (403 Forbidden)", res.status_code == 403, res.text)

        # Test 4.3: Project Manager attempts Admin-only action (POST /users) -> 403 Forbidden
        res = client.post("/api/v1/users", headers={"Authorization": f"Bearer {pm_token}"}, json={
            "name": "Should Fail PM",
            "email": "fail_pm@example.com",
            "password": password,
            "role": "developer"
        })
        record_test("Project Manager is rejected from Admin-only POST /users (403 Forbidden)", res.status_code == 403, res.text)

        # Test 4.4: require_admin_or_manager dependency check
        # Admin accessing /test-rbac/admin-or-manager -> 200 OK
        res = client.get("/test-rbac/admin-or-manager", headers={"Authorization": f"Bearer {admin_token}"})
        record_test("Admin accesses require_admin_or_manager route (200 OK)", res.status_code == 200, res.text)

        # Project Manager accessing /test-rbac/admin-or-manager -> 200 OK
        res = client.get("/test-rbac/admin-or-manager", headers={"Authorization": f"Bearer {pm_token}"})
        record_test("Project Manager accesses require_admin_or_manager route (200 OK)", res.status_code == 200, res.text)

        # Developer accessing /test-rbac/admin-or-manager -> 403 Forbidden
        res = client.get("/test-rbac/admin-or-manager", headers={"Authorization": f"Bearer {dev_token}"})
        record_test("Developer is rejected from require_admin_or_manager route (403 Forbidden)", res.status_code == 403, res.text)

        # -------------------------------------------------------------
        # 5. Profile Self-Update Tests
        # -------------------------------------------------------------
        print("\n--- 5. Profile Self-Update Tests ---")
        
        # Test 5.1: Developer updates their own display name via PATCH /users/me
        new_name = "Test Developer Renamed"
        res = client.patch("/api/v1/users/me", headers={"Authorization": f"Bearer {dev_token}"}, json={
            "name": new_name
        })
        record_test("User updates own display name via PATCH /users/me", res.status_code == 200 and res.json().get("name") == new_name, res.text)

    finally:
        print("\n--- Cleaning up test records ---")
        cleanup_test_user(dev_email)
        cleanup_test_user(escalation_email)
        cleanup_test_user(admin_email)
        cleanup_test_user(admin_created_pm_email)
        print("Cleanup completed.")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
