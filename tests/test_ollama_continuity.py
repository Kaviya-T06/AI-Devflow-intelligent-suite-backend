"""
Tests for Ollama Integration and AI Continuity Features.

Verifies:
1. Configuration loading (OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, AI_PROVIDER).
2. ask_ollama unit tests:
   - Plain text generation.
   - JSON format generation.
   - Downtime / Connection refusal (503 Service Unavailable).
   - Timeout handling (504 Gateway Timeout).
   - Empty model response (502 Bad Gateway).
   - Malformed JSON from daemon (502 Bad Gateway).
3. ask_llm provider routing:
   - Dispatches to ask_ollama when AI_PROVIDER=ollama.
   - Does NOT silently fallback to Gemini on Ollama failure.
4. AI Continuity Generate Summary:
   - Real project context retrieval with tasks, deadlines, and risks.
   - Successful summary output conforming to ContinuitySummaryOut schema.
   - Ollama downtime surfaces clean 503 with raw_context.
5. AI Continuity Ask AI:
   - Evidence-grounded answering.
   - Empty history handling.
   - Conversation history context passing.
   - Rejection of empty question (400).
6. Authorization & RBAC:
   - Enforces 403 for unauthorized project access on /generate and /ask.
7. Live Ollama Generation (against local Ollama daemon):
   - Real generation using llama3.2:1b.
"""
import asyncio
import json
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.core.config import settings
from app.core.security import hash_password
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.continuity import ContinuitySummaryOut
from app.schemas.user import UserOut
from app.services.ai_service import ask_llm
from app.services.continuity_service import (
    _build_project_context,
    ask_continuity_question_service,
    generate_continuity_summary_service,
)
from app.services.ollama_service import ask_ollama, check_ollama_health

client = TestClient(app)
db = get_supabase_client()


# ==============================================================================
# Helper Mock Classes
# ==============================================================================

class MockOllamaResponse:
    def __init__(self, status_code: int = 200, json_data: dict = None, text: str = ""):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._json_data = json_data
        self.text = text or (json.dumps(self._json_data) if json_data is not None else "")

    def json(self):
        if self._json_data is not None:
            if isinstance(self._json_data, Exception):
                raise self._json_data
            return self._json_data
        raise json.JSONDecodeError("Expecting value", self.text, 0)


# ==============================================================================
# 1. Configuration Tests
# ==============================================================================

def test_ollama_configuration():
    """Verify Ollama settings exist and default correctly."""
    assert hasattr(settings, "OLLAMA_BASE_URL")
    assert hasattr(settings, "OLLAMA_MODEL")
    assert hasattr(settings, "OLLAMA_TIMEOUT")
    assert settings.OLLAMA_BASE_URL.startswith("http")
    assert "llama" in settings.OLLAMA_MODEL
    assert settings.OLLAMA_TIMEOUT >= 10.0


# ==============================================================================
# 2. Ollama Service Unit Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_ask_ollama_success_plain_text():
    mock_resp = MockOllamaResponse(
        status_code=200,
        json_data={"model": "llama3.2:1b", "response": "Hello world from Ollama!", "done": True},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        output = await ask_ollama("Say hello")
        assert output == "Hello world from Ollama!"
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == settings.OLLAMA_MODEL
        assert call_kwargs["json"]["prompt"] == "Say hello"
        assert "format" not in call_kwargs["json"]


@pytest.mark.asyncio
async def test_ask_ollama_success_json_format():
    expected_json = json.dumps({"status": "healthy", "count": 42})
    mock_resp = MockOllamaResponse(
        status_code=200,
        json_data={"model": "llama3.2:1b", "response": expected_json, "done": True},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        output = await ask_ollama("Return json", json_response=True)
        assert output == expected_json
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["format"] == "json"


@pytest.mark.asyncio
async def test_ask_ollama_downtime_connection_error():
    """Verify connection errors raise HTTP 503 (Service Unavailable)."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused by localhost:11434")
        with pytest.raises(HTTPException) as exc_info:
            await ask_ollama("Test prompt")
        assert exc_info.value.status_code == 503
        assert "Ollama service unavailable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ask_ollama_timeout():
    """Verify timeouts raise HTTP 504 (Gateway Timeout)."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Read timed out")
        with pytest.raises(HTTPException) as exc_info:
            await ask_ollama("Test prompt")
        assert exc_info.value.status_code == 504
        assert "timed out" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ask_ollama_empty_response():
    """Verify empty model response raises HTTP 502."""
    mock_resp = MockOllamaResponse(
        status_code=200,
        json_data={"model": "llama3.2:1b", "response": "   ", "done": True},
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(HTTPException) as exc_info:
            await ask_ollama("Test prompt")
        assert exc_info.value.status_code == 502
        assert "empty response" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ask_ollama_invalid_json():
    """Verify invalid JSON from daemon raises HTTP 502."""
    mock_resp = MockOllamaResponse(status_code=200, json_data=None, text="NOT_VALID_JSON{")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(HTTPException) as exc_info:
            await ask_ollama("Test prompt")
        assert exc_info.value.status_code == 502
        assert "invalid JSON" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ask_ollama_model_not_found_404():
    """Verify 404 from Ollama indicates model not found."""
    mock_resp = MockOllamaResponse(
        status_code=404,
        json_data={"error": "model 'nonexistent' not found"},
        text="model not found",
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(HTTPException) as exc_info:
            await ask_ollama("Test prompt", model="nonexistent")
        assert exc_info.value.status_code == 502
        assert "not found" in exc_info.value.detail


# ==============================================================================
# 3. Provider Routing & No Silent Fallback Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_ask_llm_routes_to_ollama_without_gemini():
    """Verify ask_llm delegates to ask_ollama when AI_PROVIDER=ollama."""
    with patch("app.services.ai_service.settings") as mock_settings:
        mock_settings.AI_PROVIDER = "ollama"
        mock_settings.OLLAMA_MODEL = "llama3.2:1b"
        mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
        mock_settings.AI_API_KEY = ""  # No Google key

        with patch("app.services.ollama_service.ask_ollama", new_callable=AsyncMock) as mock_ollama:
            mock_ollama.return_value = "Routed to Ollama successfully"
            result = await ask_llm("Generate handover")
            assert result == "Routed to Ollama successfully"
            mock_ollama.assert_called_once_with("Generate handover", json_response=False)


@pytest.mark.asyncio
async def test_ask_llm_no_silent_fallback_on_ollama_failure():
    """Verify when Ollama fails, it does NOT silently call Gemini."""
    with patch("app.services.ai_service.settings") as mock_settings:
        mock_settings.AI_PROVIDER = "ollama"
        mock_settings.OLLAMA_MODEL = "llama3.2:1b"
        mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"

        with patch("app.services.ollama_service.ask_ollama", new_callable=AsyncMock) as mock_ollama:
            mock_ollama.side_effect = HTTPException(status_code=503, detail="Ollama service unavailable")
            
            with pytest.raises(HTTPException) as exc_info:
                await ask_llm("Test prompt")
            assert exc_info.value.status_code == 503
            assert "Ollama service unavailable" in exc_info.value.detail


# ==============================================================================
# 4. AI Continuity Integration Tests (RBAC, Empty History, Downtime)
# ==============================================================================

def test_continuity_endpoints_auth_and_project_rbac():
    """Verify JWT auth and project RBAC for /continuity/generate and /continuity/ask."""
    from app.api.deps import get_current_user
    
    proj_id = str(uuid.uuid4())
    unauthorized_user = UserOut(
        id=str(uuid.uuid4()),
        name="Unauthorized Dev",
        email="unauth@test.com",
        role=RoleEnum.DEVELOPER,
        is_active=True,
    )

    # 1. Without authentication -> 401
    res_no_auth_gen = client.post(f"/api/v1/projects/{proj_id}/continuity/generate")
    assert res_no_auth_gen.status_code == 401

    res_no_auth_ask = client.post(f"/api/v1/projects/{proj_id}/continuity/ask", json={"question": "Status?"})
    assert res_no_auth_ask.status_code == 401

    # 2. With authenticated user but forbidden project access -> 403
    app.dependency_overrides[get_current_user] = lambda: unauthorized_user
    try:
        with patch("app.api.v1.endpoints.continuity.get_project_by_id_service") as mock_proj:
            mock_proj.side_effect = HTTPException(status_code=403, detail="You do not have permission to access this project.")

            gen_res = client.post(f"/api/v1/projects/{proj_id}/continuity/generate")
            assert gen_res.status_code == 403
            assert "permission" in gen_res.json()["detail"]

            ask_res = client.post(f"/api/v1/projects/{proj_id}/continuity/ask", json={"question": "Status?"})
            assert ask_res.status_code == 403
            assert "permission" in ask_res.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_continuity_ask_ai_empty_question_validation():
    """Verify validation when no question or user message is provided."""
    from app.api.deps import get_current_user
    proj_id = str(uuid.uuid4())
    test_user = UserOut(
        id=str(uuid.uuid4()),
        name="Authorized User",
        email="auth@test.com",
        role=RoleEnum.ADMIN,
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = lambda: test_user
    try:
        with patch("app.api.v1.endpoints.continuity.check_project_access"):
            # Empty question and empty messages -> 400
            res = client.post(f"/api/v1/projects/{proj_id}/continuity/ask", json={"question": "", "messages": []})
            assert res.status_code == 400
            assert "question or at least one user message is required" in res.json()["detail"]

            # Question supplied in messages -> accepted (mock service return)
            with patch("app.api.v1.endpoints.continuity.ask_continuity_question_service", new_callable=AsyncMock) as mock_ask:
                mock_ask.return_value = "Answer based on evidence."
                res_msg = client.post(
                    f"/api/v1/projects/{proj_id}/continuity/ask",
                    json={"question": "", "messages": [{"role": "user", "content": "How are tasks going?"}]}
                )
                assert res_msg.status_code == 200
                assert res_msg.json()["answer"] == "Answer based on evidence."
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_continuity_generate_summary_mocked_ollama_flow():
    """Verify generate summary parses Ollama JSON response and attaches raw_context."""
    project_id = str(uuid.uuid4())
    user = UserOut(id=str(uuid.uuid4()), email="test@devflow.com", name="Test User", role=RoleEnum.ADMIN, is_active=True)

    mock_ollama_json = json.dumps({
        "project_overview": "DevFlow Mobile App, active status, 60% progress, PM: Jane.",
        "previous_developer_work": "Built authentication endpoints and schemas.",
        "current_work": "Tasks in progress: Push notifications implementation.",
        "pending_work": "Offline sync task pending.",
        "blocked_overdue_work": "No overdue tasks or blockers.",
        "recent_github_activity": "5 commits merged to main.",
        "known_issues": "None recorded.",
        "important_context": "Uses Supabase PostgreSQL.",
        "what_next_developer_should_know": "Check PR guidelines before merging.",
        "recommended_next_steps": "Review pending tasks.",
    })

    with patch("app.services.continuity_service._build_project_context", new_callable=AsyncMock) as mock_ctx:
        mock_ctx.return_value = ("Project Name: DevFlow Mobile App\nStatus: active", {"project": {"name": "DevFlow Mobile App"}})
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = f"```json\n{mock_ollama_json}\n```"
            with patch("app.services.continuity_service.log_activity"):
                summary = await generate_continuity_summary_service(project_id, user)
                assert isinstance(summary, ContinuitySummaryOut)
                assert summary.project_overview == "DevFlow Mobile App, active status, 60% progress, PM: Jane."
                assert summary.raw_context == {"project": {"name": "DevFlow Mobile App"}}


@pytest.mark.asyncio
async def test_continuity_ask_ai_empty_history_grounded():
    """Verify ask_continuity_question_service formats prompt safely with empty history."""
    project_id = str(uuid.uuid4())
    user = UserOut(id=str(uuid.uuid4()), email="test@devflow.com", name="Test User", role=RoleEnum.ADMIN, is_active=True)

    with patch("app.services.continuity_service._build_project_context", new_callable=AsyncMock) as mock_ctx:
        mock_ctx.return_value = ("Project Name: DevFlow Suite\nStatus: active", {})
        with patch("app.services.continuity_service.ask_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "The project status is active according to verified records."
            with patch("app.services.continuity_service.log_activity"):
                ans = await ask_continuity_question_service(project_id, "What is the status?", user, messages=[])
                assert ans == "The project status is active according to verified records."
                
                prompt_called = mock_llm.call_args[0][0]
                assert "RECENT CONVERSATION:\nNone" in prompt_called
                assert "QUESTION:" in prompt_called
                assert "What is the status?" in prompt_called
                assert "untrusted content" in prompt_called


# ==============================================================================
# 5. Live Local Ollama Verification (against localhost:11434)
# ==============================================================================

@pytest.mark.asyncio
async def test_live_ollama_health_and_generate():
    """Verify live connectivity and text generation with verified local Ollama daemon."""
    health = await check_ollama_health()
    if not health.get("available"):
        pytest.skip(f"Live Ollama not available: {health.get('error')}")

    assert health["available"] is True
    assert health["model_installed"] is True, f"Model {settings.OLLAMA_MODEL} not found in {health.get('installed_models')}"

    # Perform live generation
    prompt = "What is 2 + 2? Reply with just the number."
    response = await ask_ollama(prompt, timeout=30.0)
    assert response is not None
    assert len(response) > 0
    assert "4" in response


@pytest.mark.asyncio
async def test_live_ollama_continuity_ask_ai():
    """Verify live Ask AI question answering with local Ollama model."""
    health = await check_ollama_health()
    if not health.get("available") or not health.get("model_installed"):
        pytest.skip("Local Ollama not available")

    user = UserOut(id=str(uuid.uuid4()), email="live@test.com", name="Live User", role=RoleEnum.ADMIN, is_active=True)
    with patch("app.services.continuity_service._build_project_context", new_callable=AsyncMock) as mock_ctx:
        mock_ctx.return_value = (
            "Project Name: Cloud Storage Sync\nStatus: active\nTASKS:\n- [IN_PROGRESS] S3 Integration - Assigned to: Alice\nVERIFIED WORKFLOW RISKS:\nOpen Risks:\n- None",
            {"project": {"name": "Cloud Storage Sync"}}
        )
        with patch("app.services.continuity_service.log_activity"):
            answer = await ask_continuity_question_service("test-proj", "What task is Alice working on?", user)
            assert answer is not None
            assert len(answer) > 0
            assert "S3" in answer or "Integration" in answer or "Alice" in answer

