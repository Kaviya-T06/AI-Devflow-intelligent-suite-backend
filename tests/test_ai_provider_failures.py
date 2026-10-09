"""
Tests for AI provider failures (rate limits, timeouts, authentication errors).
"""
import os
import sys
import uuid
import json
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password

client = TestClient(app)
db = get_supabase_client()

def run_tests():
    print("\n=======================================================")
    print("AI DevFlow -- AI Provider Failures Tests")
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
    proj_id = str(uuid.uuid4())

    try:
        # Seed users
        db.table("users").insert([
            {"id": admin_id, "name": "Admin User", "email": f"admin_{suffix}@test.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
        ]).execute()

        # Seed projects
        db.table("projects").insert([
            {"id": proj_id, "name": f"Project {suffix}", "project_manager_id": admin_id, "status": "active", "progress": 50},
        ]).execute()

        # Auth
        res = client.post("/api/v1/auth/login", json={"email": f"admin_{suffix}@test.com", "password": password})
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Mock an httpx Response
        class MockResponse:
            def __init__(self, status_code, json_data, text=""):
                self.status_code = status_code
                self.is_success = 200 <= status_code < 300
                self._json_data = json_data
                self.text = text
            def json(self):
                return self._json_data

        import httpx

        # TEST 1: Quota Exceeded (429)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MockResponse(429, {"error": {"message": "Quota exceeded"}})
            
            res = client.post(f"/api/v1/projects/{proj_id}/continuity/generate", headers=headers)
            record_test("1. Generate endpoint returns 429 on quota exceeded", res.status_code == 429, res.text)
            
            detail = res.json().get("detail", {})
            record_test("2. Detail contains raw_context despite error", isinstance(detail, dict) and "raw_context" in detail, str(detail))

        # TEST 2: Authentication Error (401 -> 502 mapped)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MockResponse(401, {"error": {"message": "Invalid API key"}})
            
            res = client.post(f"/api/v1/projects/{proj_id}/continuity/generate", headers=headers)
            record_test("3. Generate endpoint returns 502 on invalid API key", res.status_code == 502, res.text)
            
        # TEST 3: Ask AI Quota Exceeded
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MockResponse(429, {"error": {"message": "Quota exceeded"}})
            
            res = client.post(f"/api/v1/projects/{proj_id}/continuity/ask", headers=headers, json={"question": "test"})
            record_test("4. Ask endpoint returns 429 on quota exceeded", res.status_code == 429, res.text)
            
        # TEST 4: Timeout Exception
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            # Note: httpx.AsyncClient.post throws exceptions during connection phase before getting a response.
            mock_post.side_effect = httpx.TimeoutException("Timeout")
            
            res = client.post(f"/api/v1/projects/{proj_id}/continuity/ask", headers=headers, json={"question": "test"})
            record_test("5. Ask endpoint returns 502 on timeout", res.status_code == 502, res.text)

    finally:
        try:
            db.table("projects").delete().eq("id", proj_id).execute()
            db.table("users").delete().eq("id", admin_id).execute()
        except:
            pass

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================")
    if passed_count < total_count:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
