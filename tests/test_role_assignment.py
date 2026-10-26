"""
Comprehensive test suite for AI DevFlow user registration, role assignment, and security validation.

Covers:
1. Registration Defaults (public registration receives 'developer')
2. Attempts to Self-Assign Privileged Roles (unauthorized admin/pm requests safely fall back or 403)
3. Authorized Role Assignment via Trusted Invite / Approval Mechanism (Admin & PM invite codes)
4. Authorized Role Assignment via Admin Workflow (POST /users and PATCH /users/{id})
5. Non-Admin Role Self-Escalation Prevention (PATCH /users/me cannot change role)
6. Login Role Persistence (DB role is preserved and returned on login)
7. JWT & Current-User Role Consistency (/users/me and decoded JWT claims match DB)
8. Existing Account Integrity (verifies existing roles remain intact)
"""
import os
import sys
import uuid
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.core.config import settings
from app.core.security import hash_password, decode_access_token
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum

client = TestClient(app)
db = get_supabase_client()


def cleanup_user(email: str):
    """Safely cleanup test user from DB."""
    try:
        db.table("users").delete().eq("email", email).execute()
    except Exception as exc:
        print(f"Cleanup warning for {email}: {exc}")


def test_public_registration_default_role():
    """Test 1: Standard public registration defaults to 'developer' role."""
    suffix = str(uuid.uuid4())[:8]
    email = f"test_pub_dev_{suffix}@example.com"
    pwd = "SecurePassword123!"

    try:
        res = client.post("/api/v1/auth/register", json={
            "name": "Public User",
            "email": email,
            "password": pwd,
        })
        assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"
        data = res.json()
        assert data["user"]["role"] == "developer"

        # Verify DB directly
        db_user = db.table("users").select("role").eq("email", email).single().execute()
        assert db_user.data["role"] == "developer"
    finally:
        cleanup_user(email)


def test_self_escalation_without_invite_code_falls_back_to_developer():
    """Test 2: Public registration requesting 'admin' or 'project_manager' without invite code safely falls back to 'developer'."""
    suffix = str(uuid.uuid4())[:8]
    email_admin_attempt = f"test_hacker_admin_{suffix}@example.com"
    email_pm_attempt = f"test_hacker_pm_{suffix}@example.com"
    pwd = "SecurePassword123!"

    try:
        # Attempt self-escalation to admin
        res1 = client.post("/api/v1/auth/register", json={
            "name": "Hacker Admin",
            "email": email_admin_attempt,
            "password": pwd,
            "role": "admin",
        })
        assert res1.status_code == 201
        assert res1.json()["user"]["role"] == "developer"
        db_u1 = db.table("users").select("role").eq("email", email_admin_attempt).single().execute()
        assert db_u1.data["role"] == "developer"

        # Attempt self-escalation to project_manager
        res2 = client.post("/api/v1/auth/register", json={
            "name": "Hacker PM",
            "email": email_pm_attempt,
            "password": pwd,
            "role": "project_manager",
        })
        assert res2.status_code == 201
        assert res2.json()["user"]["role"] == "developer"
        db_u2 = db.table("users").select("role").eq("email", email_pm_attempt).single().execute()
        assert db_u2.data["role"] == "developer"
    finally:
        cleanup_user(email_admin_attempt)
        cleanup_user(email_pm_attempt)


def test_registration_with_invalid_invite_code_is_rejected():
    """Test 3: Providing an invalid invite code for privileged role returns 403 Forbidden."""
    suffix = str(uuid.uuid4())[:8]
    email = f"test_bad_code_{suffix}@example.com"
    pwd = "SecurePassword123!"

    try:
        res = client.post("/api/v1/auth/register", json={
            "name": "Attacker",
            "email": email,
            "password": pwd,
            "role": "admin",
            "invite_code": "INVALID-SECRET-9999",
        })
        assert res.status_code == 403
        assert "Invalid invitation or authorization code" in res.json()["detail"]

        # Confirm user was NOT created in DB
        db_u = db.table("users").select("id").eq("email", email).execute()
        assert len(db_u.data) == 0
    finally:
        cleanup_user(email)


def test_authorized_role_assignment_via_invite_code():
    """Test 4: Registering with valid invitation code assigns authorized Admin and PM roles."""
    suffix = str(uuid.uuid4())[:8]
    admin_email = f"test_auth_admin_{suffix}@example.com"
    pm_email = f"test_auth_pm_{suffix}@example.com"
    pwd = "SecurePassword123!"

    admin_code = getattr(settings, "ADMIN_INVITE_CODE", "DEVFLOW-ADMIN-SECURE-KEY")
    pm_code = getattr(settings, "MANAGER_INVITE_CODE", "DEVFLOW-PM-SECURE-KEY")

    try:
        # Authorized Admin registration
        res_admin = client.post("/api/v1/auth/register", json={
            "name": "Authorized Admin",
            "email": admin_email,
            "password": pwd,
            "role": "admin",
            "invite_code": admin_code,
        })
        assert res_admin.status_code == 201, f"Admin reg failed: {res_admin.text}"
        assert res_admin.json()["user"]["role"] == "admin"
        db_admin = db.table("users").select("role").eq("email", admin_email).single().execute()
        assert db_admin.data["role"] == "admin"

        # Authorized PM registration
        res_pm = client.post("/api/v1/auth/register", json={
            "name": "Authorized PM",
            "email": pm_email,
            "password": pwd,
            "role": "project_manager",
            "invite_code": pm_code,
        })
        assert res_pm.status_code == 201, f"PM reg failed: {res_pm.text}"
        assert res_pm.json()["user"]["role"] == "project_manager"
        db_pm = db.table("users").select("role").eq("email", pm_email).single().execute()
        assert db_pm.data["role"] == "project_manager"
    finally:
        cleanup_user(admin_email)
        cleanup_user(pm_email)


def test_authorized_admin_workflow_user_creation_and_update():
    """Test 5: Authorized Admin workflow creates and updates users across roles."""
    suffix = str(uuid.uuid4())[:8]
    admin_email = f"workflow_admin_{suffix}@example.com"
    created_user_email = f"created_user_{suffix}@example.com"
    pwd = "SecurePassword123!"

    admin_id = str(uuid.uuid4())
    db.table("users").insert({
        "id": admin_id,
        "name": "Workflow Admin",
        "email": admin_email,
        "password_hash": hash_password(pwd),
        "role": "admin",
        "is_active": True,
    }).execute()

    try:
        # Login as Admin
        login_res = client.post("/api/v1/auth/login", json={"email": admin_email, "password": pwd})
        assert login_res.status_code == 200
        admin_token = login_res.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {admin_token}"}

        # Admin creates PM user via POST /api/v1/users
        create_res = client.post(
            "/api/v1/users",
            headers=auth_headers,
            json={
                "name": "PM Via Admin",
                "email": created_user_email,
                "password": pwd,
                "role": "project_manager",
            },
        )
        assert create_res.status_code == 201, f"Admin create failed: {create_res.text}"
        new_user = create_res.json()
        assert new_user["role"] == "project_manager"
        created_id = new_user["id"]

        # Admin updates user role to admin via PATCH /api/v1/users/{id}
        patch_res = client.patch(
            f"/api/v1/users/{created_id}",
            headers=auth_headers,
            json={"role": "admin"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["role"] == "admin"

        # Verify DB directly
        db_u = db.table("users").select("role").eq("id", created_id).single().execute()
        assert db_u.data["role"] == "admin"
    finally:
        cleanup_user(admin_email)
        cleanup_user(created_user_email)


def test_login_role_persistence_and_jwt_consistency():
    """Test 6: DB role is authoritative across Login, JWT claims, and /users/me."""
    suffix = str(uuid.uuid4())[:8]
    roles_to_test = ["admin", "project_manager", "developer"]
    pwd = "SecurePassword123!"

    created_emails = []

    try:
        for r in roles_to_test:
            u_id = str(uuid.uuid4())
            u_email = f"persist_{r}_{suffix}@example.com"
            created_emails.append(u_email)

            db.table("users").insert({
                "id": u_id,
                "name": f"User {r}",
                "email": u_email,
                "password_hash": hash_password(pwd),
                "role": r,
                "is_active": True,
            }).execute()

            # 1. Login
            res = client.post("/api/v1/auth/login", json={"email": u_email, "password": pwd})
            assert res.status_code == 200
            data = res.json()
            assert data["user"]["role"] == r, f"Login returned wrong role for {r}"

            token = data["access_token"]
            # 2. JWT claims check
            claims = decode_access_token(token)
            assert claims["sub"] == u_id
            assert claims["role"] == r, f"JWT claims had wrong role for {r}"

            # 3. /users/me check
            me_res = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
            assert me_res.status_code == 200
            me_data = me_res.json()
            assert me_data["role"] == r, f"/users/me had wrong role for {r}"

            # 4. Confirm DB row role was NOT altered
            db_row = db.table("users").select("role").eq("id", u_id).single().execute()
            assert db_row.data["role"] == r
    finally:
        for e in created_emails:
            cleanup_user(e)


def test_self_update_cannot_escalate_role():
    """Test 7: Regular user cannot change their role via PATCH /users/me."""
    suffix = str(uuid.uuid4())[:8]
    dev_email = f"dev_no_escalate_{suffix}@example.com"
    pwd = "SecurePassword123!"
    dev_id = str(uuid.uuid4())

    db.table("users").insert({
        "id": dev_id,
        "name": "Dev Cannot Escalate",
        "email": dev_email,
        "password_hash": hash_password(pwd),
        "role": "developer",
        "is_active": True,
    }).execute()

    try:
        login_res = client.post("/api/v1/auth/login", json={"email": dev_email, "password": pwd})
        token = login_res.json()["access_token"]

        # Attempt to pass role to /users/me
        patch_res = client.patch(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Dev Renamed", "role": "admin"},
        )
        assert patch_res.status_code == 200
        # Role must still be developer
        assert patch_res.json()["role"] == "developer"

        # Verify DB directly
        db_u = db.table("users").select("role").eq("id", dev_id).single().execute()
        assert db_u.data["role"] == "developer"
    finally:
        cleanup_user(dev_email)


def test_existing_accounts_integrity():
    """Test 8: Verify existing accounts in database retain their exact assigned roles."""
    # Query all users and verify role values adhere strictly to supported roles
    res = db.table("users").select("email, role").execute()
    assert len(res.data) > 0, "Users table should have existing accounts"

    valid_roles = {"admin", "developer", "project_manager"}
    role_counts = {r: 0 for r in valid_roles}

    for user in res.data:
        r = user["role"]
        assert r in valid_roles, f"User {user['email']} has invalid role: {r}"
        role_counts[r] += 1

    # Verify that Admin, Developer, and Project Manager accounts exist
    assert role_counts["admin"] > 0, "Expected at least 1 Admin user in DB"
    assert role_counts["developer"] > 0, "Expected at least 1 Developer user in DB"
    assert role_counts["project_manager"] > 0, "Expected at least 1 Project Manager user in DB"
    print(f"Role distribution in DB: {role_counts}")


if __name__ == "__main__":
    print("\nRunning test_role_assignment suite directly...")
    pytest.main(["-v", __file__])
