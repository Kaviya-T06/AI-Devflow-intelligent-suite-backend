"""
Test Suite for GitHub Integration Authentication.
"""
import os
import sys
import uuid
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password
from app.core.config import settings

client = TestClient(app)
db = get_supabase_client()


def cleanup_db(ids_to_delete, table_name):
    for record_id in ids_to_delete:
        try:
            db.table(table_name).delete().eq("id", record_id).execute()
        except Exception as e:
            pass

def run_github_auth_tests():
    print("\n=======================================================")
    print("AI DevFlow -- GitHub Integration Auth Tests")
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
    project1_id = str(uuid.uuid4())

    try:
        # Seed users
        db.table("users").insert([
            {"id": admin_id, "name": "Admin", "email": f"admin_{suffix}@example.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
        ]).execute()

        # Seed projects
        db.table("projects").insert([
            {"id": project1_id, "name": "Project Auth", "project_manager_id": admin_id, "status": "active"},
        ]).execute()
        
        # Seed repo
        db.table("project_github_repositories").insert([
            {
                "project_id": project1_id,
                "owner": "testowner",
                "repository_name": "testrepo",
                "full_name": "testowner/testrepo",
                "html_url": "https://github.com/testowner/testrepo",
                "github_repository_id": "12345",
                "connected_by": admin_id
            }
        ]).execute()

        res = client.post("/api/v1/auth/login", json={"email": f"admin_{suffix}@example.com", "password": password})
        admin_token = res.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        original_token = getattr(settings, "GITHUB_TOKEN", "")

        # -------------------------------------------------------------
        # 1. GitHub API requests include authentication when configured.
        # -------------------------------------------------------------
        settings.GITHUB_TOKEN = "test-fake-token"
        
        import httpx
        
        class MockResponse:
            def __init__(self, status_code, json_data, headers=None):
                self.status_code = status_code
                self._json_data = json_data
                self.headers = headers or {}
            
            def json(self):
                return self._json_data
                
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError("Error", request=MagicMock(), response=self)
                    
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = MockResponse(200, [{"sha": "abc", "commit": {"message": "test", "author": {"name": "test", "date": "test"}}, "html_url": "http"}])
            
            # Use pytest-asyncio loop or just rely on fastapi test client which handles async automatically
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            
            # The async client is a context manager, we patched `get`. 
            # We need to verify what headers were passed to AsyncClient initialization if possible, 
            # or we can patch AsyncClient itself.
            record_test("1. Successfully mocked commit fetch with 200", res.status_code == 200, res.text)
            
        # A better way to check the headers is to patch the AsyncClient init
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(200, [{"sha": "abc", "commit": {"message": "test", "author": {"name": "test", "date": "test"}}, "html_url": "http"}])
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            
            args, kwargs = MockClient.call_args
            headers = kwargs.get("headers", {})
            auth_header = headers.get("Authorization")
            record_test("2. GITHUB_TOKEN is included in request headers", auth_header == "Bearer test-fake-token", str(headers))
            
        # -------------------------------------------------------------
        # 3. Missing GitHub authentication handled cleanly
        # -------------------------------------------------------------
        settings.GITHUB_TOKEN = ""
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(200, [{"sha": "abc", "commit": {"message": "test", "author": {"name": "test", "date": "test"}}, "html_url": "http"}])
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            
            args, kwargs = MockClient.call_args
            headers = kwargs.get("headers", {})
            auth_header = headers.get("Authorization")
            record_test("3. Missing token means no Authorization header", auth_header is None, str(headers))
            
        # -------------------------------------------------------------
        # 4. GitHub 401 is handled correctly
        # -------------------------------------------------------------
        settings.GITHUB_TOKEN = "bad-token"
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(401, {"message": "Bad credentials"})
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("4. 401 Unauthorized from GitHub returns 502 with auth error", res.status_code == 502 and "authentication failed" in res.json()["detail"], res.text)
            record_test("4b. No token is exposed in response", "bad-token" not in res.text, res.text)
            
        # -------------------------------------------------------------
        # 5. GitHub rate limit (403) handled correctly
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(403, {"message": "API rate limit exceeded"}, headers={"x-ratelimit-remaining": "0"})
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("5. 403 Rate Limit returns 502 with rate limit message", res.status_code == 502 and "rate limit" in res.json()["detail"], res.text)
            
        # -------------------------------------------------------------
        # 6. GitHub 404 is handled correctly
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(404, {"message": "Not Found"})
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("6. 404 Not Found returns 404", res.status_code == 404 and "not found" in res.json()["detail"], res.text)

        settings.GITHUB_TOKEN = original_token

    finally:
        cleanup_db([project1_id], "project_github_repositories")
        cleanup_db([project1_id], "projects")
        cleanup_db([admin_id], "users")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_github_auth_tests()
