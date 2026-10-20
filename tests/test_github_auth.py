"""
Test Suite for GitHub Integration Authentication and Error Handling.
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
        except Exception:
            pass

def run_github_auth_tests():
    print("\n=======================================================")
    print("AI DevFlow -- GitHub Integration Auth & API Error Tests")
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
    unconnected_project_id = str(uuid.uuid4())

    try:
        # Seed users
        db.table("users").insert([
            {"id": admin_id, "name": "Admin", "email": f"admin_{suffix}@example.com", "password_hash": hash_password(password), "role": "admin", "is_active": True},
        ]).execute()

        # Seed projects: project1 (connected) and unconnected_project
        db.table("projects").insert([
            {"id": project1_id, "name": "Project Auth Connected", "project_manager_id": admin_id, "status": "active"},
            {"id": unconnected_project_id, "name": "Project Unconnected", "project_manager_id": admin_id, "status": "active"},
        ]).execute()
        
        # Seed repo for project1
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

        import httpx

        class MockResponse:
            def __init__(self, status_code, json_data, headers=None, text=""):
                self.status_code = status_code
                self._json_data = json_data
                self.headers = headers or {}
                self.text = text or str(json_data)
            
            def json(self):
                return self._json_data
                
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError("Error", request=MagicMock(), response=self)

        # -------------------------------------------------------------
        # 1. Valid GitHub API requests return 200 with data
        # -------------------------------------------------------------
        settings.GITHUB_TOKEN = "test-fake-token"
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value = MockResponse(200, [
                {"sha": "abc1234", "commit": {"message": "Initial commit", "author": {"name": "Alice", "date": "2026-10-20"}}, "html_url": "https://github.com/testowner/testrepo/commit/abc1234"}
            ])
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("1. Successfully fetched commits with 200", res.status_code == 200 and len(res.json()) == 1, res.text)

        # -------------------------------------------------------------
        # 2. GITHUB_TOKEN is included in request headers
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(200, [{"sha": "abc", "commit": {"message": "test", "author": {"name": "test", "date": "test"}}, "html_url": "http"}])
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            
            args, kwargs = MockClient.call_args
            headers = kwargs.get("headers", {})
            auth_header = headers.get("Authorization")
            record_test("2. GITHUB_TOKEN is included in request headers as Bearer <token>", auth_header == "Bearer test-fake-token", str(headers))

        # -------------------------------------------------------------
        # 3. Missing/empty GitHub token handled cleanly (no broken Auth header)
        # -------------------------------------------------------------
        settings.GITHUB_TOKEN = ""
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(200, [{"sha": "abc", "commit": {"message": "test", "author": {"name": "test", "date": "test"}}, "html_url": "http"}])
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            
            args, kwargs = MockClient.call_args
            headers = kwargs.get("headers", {})
            auth_header = headers.get("Authorization")
            record_test("3. Missing token does not include Authorization header", auth_header is None, str(headers))

        # -------------------------------------------------------------
        # 4. GitHub 401 Unauthorized returns 401 (not generic 502)
        # -------------------------------------------------------------
        settings.GITHUB_TOKEN = "bad-expired-token"
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(401, {"message": "Bad credentials"})
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("4. 401 Unauthorized from GitHub returns HTTP 401 with auth error detail", res.status_code == 401 and "authentication failed" in res.json()["detail"].lower(), res.text)
            record_test("4b. Sensitive token is not exposed in error response", "bad-expired-token" not in res.text, res.text)

        # -------------------------------------------------------------
        # 5. GitHub 403 Rate Limit returns 429 (not generic 502)
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(403, {"message": "API rate limit exceeded"}, headers={"x-ratelimit-remaining": "0"}, text="rate limit exceeded")
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("5. 403 Rate Limit returns HTTP 429 with rate limit message", res.status_code == 429 and "rate limit" in res.json()["detail"].lower(), res.text)

        # -------------------------------------------------------------
        # 6. GitHub 403 Forbidden (insufficient permissions) returns 403 (not 502)
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(403, {"message": "Must have admin rights to Repository"}, headers={"x-ratelimit-remaining": "60"}, text="forbidden")
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("6. 403 Insufficient permissions returns HTTP 403", res.status_code == 403 and "forbidden" in res.json()["detail"].lower(), res.text)

        # -------------------------------------------------------------
        # 7. GitHub 404 Not Found returns 404
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.return_value = MockResponse(404, {"message": "Not Found"})
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("7. 404 Not Found returns HTTP 404", res.status_code == 404 and "not found" in res.json()["detail"].lower(), res.text)

        # -------------------------------------------------------------
        # 8. Network timeout returns 504
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.side_effect = httpx.TimeoutException("Connection timed out")
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("8. Network timeout returns HTTP 504 Gateway Timeout", res.status_code == 504 and "timed out" in res.json()["detail"].lower(), res.text)

        # -------------------------------------------------------------
        # 9. Network connect error returns 502
        # -------------------------------------------------------------
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.get.side_effect = httpx.ConnectError("Failed to establish a new connection")
            
            res = client.get(f"/api/v1/github/projects/{project1_id}/commits", headers=admin_headers)
            record_test("9. Network connection error returns HTTP 502", res.status_code == 502 and "network error" in res.json()["detail"].lower(), res.text)

        # -------------------------------------------------------------
        # 10. Unconnected project returns 404 No GitHub repository connected
        # -------------------------------------------------------------
        res = client.get(f"/api/v1/github/projects/{unconnected_project_id}/commits", headers=admin_headers)
        record_test("10a. Commits on unconnected project returns 404 No GitHub repository connected", res.status_code == 404 and "no github repository connected" in res.json()["detail"].lower(), res.text)

        res = client.get(f"/api/v1/github/projects/{unconnected_project_id}/branches", headers=admin_headers)
        record_test("10b. Branches on unconnected project returns 404 No GitHub repository connected", res.status_code == 404 and "no github repository connected" in res.json()["detail"].lower(), res.text)

        res = client.get(f"/api/v1/github/projects/{unconnected_project_id}/pulls", headers=admin_headers)
        record_test("10c. Pulls on unconnected project returns 404 No GitHub repository connected", res.status_code == 404 and "no github repository connected" in res.json()["detail"].lower(), res.text)

        res = client.get(f"/api/v1/github/projects/{unconnected_project_id}/issues", headers=admin_headers)
        record_test("10d. Issues on unconnected project returns 404 No GitHub repository connected", res.status_code == 404 and "no github repository connected" in res.json()["detail"].lower(), res.text)

        settings.GITHUB_TOKEN = original_token

    finally:
        cleanup_db([project1_id], "project_github_repositories")
        cleanup_db([project1_id, unconnected_project_id], "projects")
        cleanup_db([admin_id], "users")

    print(f"\n=======================================================")
    print(f"Results: {passed_count}/{total_count} tests passed ({(passed_count/total_count)*100:.1f}%)")
    print(f"=======================================================\n")
    if passed_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    run_github_auth_tests()
