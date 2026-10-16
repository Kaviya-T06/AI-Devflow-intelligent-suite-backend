"""
Test suite for Phase 4: AI Project Pulse Recovery Recommendations.

Tests cover:
1. Grounded recommendations (schema validity, confidence, why it matters, references).
2. Unsupported entity rejection (hallucinated task IDs, devs, risks).
3. Quota errors (HTTP 429 fallback to safe deterministic recommendations).
4. Timeouts & provider failures (fallback to safe deterministic recommendations).
5. Malformed AI output (JSON parse failure or invalid schema).
6. Missing data & empty projects.
7. Project isolation (PM A cannot access PM B's project recommendations).
8. Health score integrity (Gemini never overrides or modifies deterministic score).
9. Live Gemini distinction (automated tests use mocks; live tests separated with skip guard).
"""
import os
import sys
import uuid
import json
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password

client = TestClient(app)
db = get_supabase_client()


@pytest.fixture(scope="module")
def pm_a_headers():
    pm_id = str(uuid.uuid4())
    pm_email = f"pm_pulse_a_{pm_id}@test.com"
    pm_pass = hash_password("password123")

    db.table("users").insert({
        "id": pm_id,
        "email": pm_email,
        "password_hash": pm_pass,
        "name": "PM Pulse Tester A",
        "role": "project_manager",
        "is_active": True
    }).execute()

    response = client.post("/api/v1/auth/login", json={"email": pm_email, "password": "password123"})
    if response.status_code != 200:
        raise RuntimeError(f"Login failed: {response.text}")
    token = response.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    yield headers, pm_id

    db.table("users").delete().eq("id", pm_id).execute()


@pytest.fixture(scope="module")
def pm_b_headers():
    pm_id = str(uuid.uuid4())
    pm_email = f"pm_pulse_b_{pm_id}@test.com"
    pm_pass = hash_password("password123")

    db.table("users").insert({
        "id": pm_id,
        "email": pm_email,
        "password_hash": pm_pass,
        "name": "PM Pulse Tester B",
        "role": "project_manager",
        "is_active": True
    }).execute()

    response = client.post("/api/v1/auth/login", json={"email": pm_email, "password": "password123"})
    if response.status_code != 200:
        raise RuntimeError(f"Login failed: {response.text}")
    token = response.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}

    yield headers, pm_id

    db.table("users").delete().eq("id", pm_id).execute()


def create_project(pm_id, start_date=None, end_date=None, progress=50, name="Pulse Test Project"):
    proj_id = str(uuid.uuid4())
    db.table("projects").insert({
        "id": proj_id,
        "name": name,
        "project_manager_id": pm_id,
        "status": "active",
        "start_date": start_date,
        "end_date": end_date,
        "progress": progress
    }).execute()
    return proj_id


def cleanup_project(proj_id):
    db.table("workflow_risks").delete().eq("project_id", proj_id).execute()
    db.table("tasks").delete().eq("project_id", proj_id).execute()
    db.table("projects").delete().eq("id", proj_id).execute()


# ---------------------------------------------------------------------------
# 1. Grounded Recommendations Test
# ---------------------------------------------------------------------------

def test_grounded_recommendations(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=50)

    task_id = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_id,
        "title": "Auth API Migration",
        "project_id": proj_id,
        "status": "IN_PROGRESS",
        "due_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    }).execute()

    risk_id = str(uuid.uuid4())
    db.table("workflow_risks").insert({
        "id": risk_id,
        "risk_type": "INFRA",
        "title": "Database Connection Pool Exhaustion",
        "description": "High load causing connection timeouts",
        "level": "Critical",
        "project_id": proj_id,
        "is_resolved": False
    }).execute()

    mock_gemini_output = json.dumps([
        {
            "id": "rec-1",
            "category": "RISK_MITIGATION",
            "title": "Mitigate Critical Risk: Database Pool",
            "explanation": "Active critical infrastructure risk is directly reducing health score.",
            "why_it_matters": "Incurs a -15 penalty and threatens upcoming releases.",
            "missing_info_or_assumptions": "Requires connection metrics from cloud provider.",
            "confidence": "High",
            "confidence_rationale": "Directly grounded in verified open critical risk.",
            "supporting_references": [
                {"type": "risk", "id": risk_id, "label": "Database Connection Pool Exhaustion"}
            ],
            "actionable_entity_id": risk_id,
            "actionable_entity_type": "risk"
        },
        {
            "id": "rec-2",
            "category": "TASK_REVIEW",
            "title": "Review Overdue Task: Auth API Migration",
            "explanation": "Due date has passed by 2 days without completion.",
            "why_it_matters": "Blocks frontend authentication flow.",
            "missing_info_or_assumptions": "Verify if PR is awaiting review.",
            "confidence": "High",
            "confidence_rationale": "Deterministic date calculation.",
            "supporting_references": [
                {"type": "task", "id": task_id, "label": "Auth API Migration"}
            ],
            "actionable_entity_id": task_id,
            "actionable_entity_type": "task"
        }
    ])

    try:
        with patch("app.services.pulse_recommendations_service.ask_llm", new=AsyncMock(return_value=mock_gemini_output)):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["project_id"] == proj_id
            assert data["source"] == "gemini"
            assert data["ai_status"] == "SUCCESS"
            # Health score check: 100 - 15 (Critical) = 85 (task is overdue, not double-penalized if tracked or -3)
            assert data["health_score"] in (82, 85)

            recs = data["recommendations"]
            assert len(recs) == 2

            rec_risk = next(r for r in recs if r["category"] == "RISK_MITIGATION")
            assert "Database" in rec_risk["title"]
            assert rec_risk["confidence"] == "High"
            assert rec_risk["supporting_references"][0]["id"] == risk_id

            rec_task = next(r for r in recs if r["category"] == "TASK_REVIEW")
            assert "Auth API Migration" in rec_task["title"]
            assert rec_task["supporting_references"][0]["id"] == task_id
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 2. Unsupported Entity Rejection Test
# ---------------------------------------------------------------------------

def test_unsupported_entity_rejection(pm_a_headers):
    """
    Test that hallucinated/unsupported task IDs or dev IDs returned by the model
    are rejected by the validation engine.
    """
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=100)

    # Legitimate task
    valid_task_id = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": valid_task_id,
        "title": "Real Valid Task",
        "project_id": proj_id,
        "status": "IN_PROGRESS",
        "due_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    }).execute()

    hallucinated_task_id = str(uuid.uuid4())
    hallucinated_dev_id = str(uuid.uuid4())

    mock_gemini_with_hallucinations = json.dumps([
        {
            "id": "rec-hallucinated",
            "category": "TASK_REVIEW",
            "title": f"Review Fake Task {hallucinated_task_id}",
            "explanation": "This task does not exist in the database.",
            "why_it_matters": "Fake impact.",
            "missing_info_or_assumptions": "None",
            "confidence": "High",
            "confidence_rationale": "Invented",
            "supporting_references": [
                {"type": "task", "id": hallucinated_task_id, "label": "Fake Task"}
            ]
        },
        {
            "id": "rec-hallucinated-dev",
            "category": "ALLOCATION_OPPORTUNITY",
            "title": "Assign Ghost Developer",
            "explanation": "Assign to developer who does not exist.",
            "why_it_matters": "Fake impact.",
            "missing_info_or_assumptions": "None",
            "confidence": "High",
            "confidence_rationale": "Invented",
            "supporting_references": [
                {"type": "developer", "id": hallucinated_dev_id, "label": "Ghost Dev"}
            ]
        },
        {
            "id": "rec-valid",
            "category": "TASK_REVIEW",
            "title": "Review Real Valid Task",
            "explanation": "This task genuinely exists and is overdue.",
            "why_it_matters": "Delays schedule.",
            "missing_info_or_assumptions": "None",
            "confidence": "High",
            "confidence_rationale": "Deterministic date check",
            "supporting_references": [
                {"type": "task", "id": valid_task_id, "label": "Real Valid Task"}
            ]
        }
    ])

    try:
        with patch("app.services.pulse_recommendations_service.ask_llm", new=AsyncMock(return_value=mock_gemini_with_hallucinations)):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            # The hallucinated recommendations must be rejected!
            recs = data["recommendations"]
            # None of the recommendations should reference the hallucinated IDs
            for r in recs:
                assert hallucinated_task_id not in r["title"]
                assert hallucinated_task_id not in r["explanation"]
                for ref in r.get("supporting_references", []):
                    assert ref["id"] != hallucinated_task_id
                    assert ref["id"] != hallucinated_dev_id

            # The legitimate task recommendation must be preserved
            assert any(r["title"] == "Review Real Valid Task" for r in recs)
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 3. Quota Error (429) Fallback Test
# ---------------------------------------------------------------------------

def test_quota_error_fallback(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=80)

    risk_id = str(uuid.uuid4())
    db.table("workflow_risks").insert({
        "id": risk_id,
        "risk_type": "SECURITY",
        "title": "Unauthenticated API Gateway",
        "description": "Gateway missing token validation",
        "level": "High",
        "project_id": proj_id,
        "is_resolved": False
    }).execute()

    try:
        # Simulate 429 quota error from ask_llm
        with patch("app.services.pulse_recommendations_service.ask_llm", side_effect=HTTPException(status_code=429, detail="Quota exceeded")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["source"] == "deterministic"
            assert data["ai_status"] == "FALLBACK"
            assert "quota-limited" in data["ai_notice"].lower()

            # Deterministic recommendation for High risk must be present
            recs = data["recommendations"]
            assert len(recs) >= 1
            risk_rec = next(r for r in recs if r["category"] == "RISK_MITIGATION")
            assert "Unauthenticated API Gateway" in risk_rec["title"]
            assert risk_rec["supporting_references"][0]["id"] == risk_id
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 4. Timeout Fallback Test
# ---------------------------------------------------------------------------

def test_timeout_fallback(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=80)

    try:
        # Simulate timeout from ask_llm
        with patch("app.services.pulse_recommendations_service.ask_llm", side_effect=HTTPException(status_code=504, detail="Upstream request timed out")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["source"] == "deterministic"
            assert data["ai_status"] == "FALLBACK"
            assert "unavailable" in data["ai_notice"].lower()
            assert len(data["recommendations"]) >= 1
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 5. Malformed AI Output Test
# ---------------------------------------------------------------------------

def test_malformed_ai_output_fallback(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=80)

    try:
        # Simulate completely broken JSON
        with patch("app.services.pulse_recommendations_service.ask_llm", new=AsyncMock(return_value="```json { broken json, not valid [")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["source"] == "deterministic"
            assert data["ai_status"] == "FALLBACK"
            assert len(data["recommendations"]) >= 1
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 6. Missing Data & Empty Project Test
# ---------------------------------------------------------------------------

def test_missing_data_and_empty_project(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id) # No tasks, no dates, progress 0

    mock_empty_recs = json.dumps([
        {
            "id": "rec-empty-1",
            "category": "GENERAL",
            "title": "Maintain Current Delivery Cadence",
            "explanation": "No tasks or risks are recorded yet. Initialize project backlog.",
            "why_it_matters": "Keeps project planning moving forward.",
            "missing_info_or_assumptions": "Requires project milestone setup.",
            "confidence": "High",
            "confidence_rationale": "Directly grounded in empty project state.",
            "supporting_references": [],
        }
    ])

    try:
        with patch("app.services.pulse_recommendations_service.ask_llm", new=AsyncMock(return_value=mock_empty_recs)):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["health_data_completeness"] == "Low"
            assert data["health_score"] == 100
            assert len(data["recommendations"]) >= 1
            # Safe general guidance recommendation for empty projects
            rec = data["recommendations"][0]
            assert "Cadence" in rec["title"] or "Momentum" in rec["title"] or "General" in rec["category"].title()
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 7. Project Isolation Test
# ---------------------------------------------------------------------------

def test_project_isolation(pm_a_headers, pm_b_headers):
    headers_a, pm_a_id = pm_a_headers
    headers_b, pm_b_id = pm_b_headers

    proj_a_id = create_project(pm_a_id, name="PM A Isolated Project")

    try:
        # PM A can access Project A
        with patch("app.services.pulse_recommendations_service.ask_llm", new=AsyncMock(return_value="[]")):
            resp_a = client.get(f"/api/v1/dashboard/projects/{proj_a_id}/pulse-recommendations", headers=headers_a)
            assert resp_a.status_code == 200

        # PM B MUST be forbidden from accessing Project A
        resp_b = client.get(f"/api/v1/dashboard/projects/{proj_a_id}/pulse-recommendations", headers=headers_b)
        assert resp_b.status_code == 403
        assert "permission" in resp_b.text.lower()
    finally:
        cleanup_project(proj_a_id)


# ---------------------------------------------------------------------------
# 8. Health Score Integrity (Gemini Cannot Override)
# ---------------------------------------------------------------------------

def test_health_score_not_overridden_by_gemini(pm_a_headers):
    """
    Verify requirement 6: Gemini must not calculate or override the health score.
    Even if Gemini attempts to claim a different health score in its text,
    the returned health_score remains 100% deterministic.
    """
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=100)

    # 1 Critical Risk = -15
    db.table("workflow_risks").insert({
        "id": str(uuid.uuid4()),
        "risk_type": "INFRA",
        "title": "Severe Outage",
        "description": "Severe network and database outage across primary region.",
        "level": "Critical",
        "project_id": proj_id,
        "is_resolved": False
    }).execute()

    # Model attempts to suggest health is 40
    mock_gemini_attempt = json.dumps([
        {
            "id": "rec-1",
            "category": "RISK_MITIGATION",
            "title": "Fix Severe Outage",
            "explanation": "I calculated your health score as 40 instead of 85.",
            "why_it_matters": "Health score drops to 40.",
            "missing_info_or_assumptions": "None",
            "confidence": "High",
            "confidence_rationale": "Model calculation",
            "supporting_references": []
        }
    ])

    try:
        with patch("app.services.pulse_recommendations_service.ask_llm", new=AsyncMock(return_value=mock_gemini_attempt)):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            # Health score MUST be exactly 85 (100 - 15), untouched by Gemini's claim of 40
            assert data["health_score"] == 85
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 9. Live Gemini Distinction Guard
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_GEMINI_TESTS"),
    reason="Live Gemini tests disabled by default for automated CI reliability. Set RUN_LIVE_GEMINI_TESTS=1 to run."
)
def test_live_gemini_recommendations(pm_a_headers):
    """
    Distinguished live test that actually calls Google Gemini API.
    Runs only when explicitly enabled by setting RUN_LIVE_GEMINI_TESTS=1.
    """
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=100)
    try:
        resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-recommendations", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["health_score"] == 100
        assert len(data["recommendations"]) >= 1
    finally:
        cleanup_project(proj_id)
