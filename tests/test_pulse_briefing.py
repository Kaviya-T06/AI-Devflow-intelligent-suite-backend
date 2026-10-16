"""
Test suite for Phase 5: AI Project Pulse Briefing.

Tests cover:
1. Grounded briefing across all 9 sections (health, completed work, active/pending, overdue, risks, activity, github, next actions, data gaps).
2. Cross-project isolation (PM A cannot access PM B's briefing).
3. Empty context and data gaps handling (missing tasks, dates, github).
4. Provider failures (HTTP 429 quota fallback to safe deterministic briefing).
5. Provider timeouts (HTTP 504 timeout fallback).
6. Malformed AI output fallback (broken JSON syntax).
7. Factual grounding & anti-hallucination (health score override rejected, commit message does not complete tasks, missing GitHub not marked failure).
8. Stale context prevention (switching between projects never mixes or reuses context).
9. Live Gemini distinction guard (automated tests use mocks; live tests separated with skip guard).
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
    pm_email = f"pm_briefing_a_{pm_id}@test.com"
    pm_pass = hash_password("password123")

    db.table("users").insert({
        "id": pm_id,
        "email": pm_email,
        "password_hash": pm_pass,
        "name": "PM Briefing Tester A",
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
    pm_email = f"pm_briefing_b_{pm_id}@test.com"
    pm_pass = hash_password("password123")

    db.table("users").insert({
        "id": pm_id,
        "email": pm_email,
        "password_hash": pm_pass,
        "name": "PM Briefing Tester B",
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


def create_project(pm_id, start_date=None, end_date=None, progress=50, name="Briefing Test Project"):
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
# 1. Grounded Briefing Test (All 9 Sections)
# ---------------------------------------------------------------------------

def test_grounded_briefing_all_sections(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, progress=60, name="Alpha Core Engine")

    # 1. Completed Task
    task_done = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_done,
        "title": "Setup OAuth Infrastructure",
        "project_id": proj_id,
        "status": "COMPLETED",
        "priority": "HIGH",
    }).execute()

    # 2. In-Progress Task
    task_ip = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_ip,
        "title": "Data Pipeline Ingestion",
        "project_id": proj_id,
        "status": "IN_PROGRESS",
        "priority": "MEDIUM",
    }).execute()

    # 3. Overdue Task
    task_od = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_od,
        "title": "Payment Webhook Reconciliation",
        "project_id": proj_id,
        "status": "IN_PROGRESS",
        "due_date": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    }).execute()

    # 4. Critical Risk (-15 pts)
    risk_id = str(uuid.uuid4())
    db.table("workflow_risks").insert({
        "id": risk_id,
        "risk_type": "INFRA",
        "title": "Redis Cache Outage",
        "description": "Cluster node memory limits exceeded",
        "level": "Critical",
        "project_id": proj_id,
        "is_resolved": False
    }).execute()

    mock_gemini_briefing = json.dumps({
        "health": {
            "status": "Caution",
            "explanation": "Health score reflects impact from 1 critical risk and unmitigated overdue work."
        },
        "completed_work": {
            "summary": "Setup OAuth Infrastructure was successfully completed."
        },
        "in_progress_and_pending": {
            "summary": "Active work is proceeding on Data Pipeline Ingestion and Payment Webhooks."
        },
        "overdue_or_blocked": {
            "summary": "Payment Webhook Reconciliation is overdue by 3 days."
        },
        "unresolved_risks": {
            "summary": "Active critical risk: Redis Cache Outage requires infrastructure mitigation."
        },
        "recent_activity": {
            "summary": "Recent activity shows active task updates."
        },
        "github_context": {
            "summary": "GitHub repository not connected; internal task tracking active."
        },
        "recommended_next_actions": {
            "actions": [
                "Mitigate Redis Cache Outage immediately",
                "Review Payment Webhook Reconciliation due date"
            ],
            "summary": "Prioritize critical infrastructure fix and overdue reconciliation."
        },
        "data_gaps": {
            "reliability_impact": "Missing schedule dates slightly reduce timeline projection certainty."
        }
    })

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", new=AsyncMock(return_value=mock_gemini_briefing)):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["project_id"] == proj_id
            assert data["project_name"] == "Alpha Core Engine"
            assert data["source"] == "gemini"
            assert data["ai_status"] == "SUCCESS"

            # Deterministic health score check: 100 - 15 (Critical) = 85 (or -3 for overdue = 82)
            assert data["health_score"] in (82, 85)

            sections = data["sections"]
            # All 9 sections present
            assert "health" in sections
            assert "completed_work" in sections
            assert "in_progress_and_pending" in sections
            assert "overdue_or_blocked" in sections
            assert "unresolved_risks" in sections
            assert "recent_activity" in sections
            assert "github_context" in sections
            assert "recommended_next_actions" in sections
            assert "data_gaps" in sections

            # Section specific validations
            assert sections["completed_work"]["tasks_count"] == 1
            assert "Setup OAuth Infrastructure" in sections["completed_work"]["task_titles"]

            assert sections["overdue_or_blocked"]["count"] >= 1
            assert sections["unresolved_risks"]["critical_count"] == 1

            # Verified facts ground truth check
            facts = data["verified_facts"]
            assert facts["total_tasks_count"] == 3
            assert len(facts["completed_tasks"]) == 1
            assert len(facts["critical_risks"]) == 1
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 2. Cross-Project Isolation Test
# ---------------------------------------------------------------------------

def test_cross_project_isolation(pm_a_headers, pm_b_headers):
    headers_a, pm_a_id = pm_a_headers
    headers_b, pm_b_id = pm_b_headers

    proj_a_id = create_project(pm_a_id, name="PM A Confidential Project")

    try:
        # PM A can access Project A briefing
        with patch("app.services.pulse_briefing_service.ask_llm", new=AsyncMock(return_value="{}")):
            resp_a = client.get(f"/api/v1/dashboard/projects/{proj_a_id}/pulse-briefing", headers=headers_a)
            assert resp_a.status_code == 200

        # PM B MUST be forbidden from accessing Project A briefing
        resp_b = client.get(f"/api/v1/dashboard/projects/{proj_a_id}/pulse-briefing", headers=headers_b)
        assert resp_b.status_code == 403
        assert "permission" in resp_b.text.lower()
    finally:
        cleanup_project(proj_a_id)


# ---------------------------------------------------------------------------
# 3. Empty Context & Data Gaps Handling
# ---------------------------------------------------------------------------

def test_empty_context_and_data_gaps(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, name="Empty Skeleton Project")

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", new=AsyncMock(return_value="{}")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["health_data_completeness"] == "Low"
            assert data["health_score"] == 100

            sections = data["sections"]
            assert sections["completed_work"]["tasks_count"] == 0
            assert sections["unresolved_risks"]["critical_count"] == 0
            assert sections["overdue_or_blocked"]["count"] == 0

            # Data gaps must be identified
            gaps = sections["data_gaps"]["gaps"]
            assert any("no tasks" in g.lower() for g in gaps)
            assert any("dates" in g.lower() for g in gaps)
            assert any("github" in g.lower() for g in gaps)
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 4. Provider Quota Error (HTTP 429) Fallback Test
# ---------------------------------------------------------------------------

def test_provider_quota_error_fallback(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, name="Quota Fallback Project")

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", side_effect=HTTPException(status_code=429, detail="Resource exhausted / quota limit")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["source"] == "deterministic"
            assert data["ai_status"] == "FALLBACK"
            assert "quota-limited" in data["ai_notice"].lower()

            sections = data["sections"]
            assert "health" in sections
            assert "completed_work" in sections
            assert "recommended_next_actions" in sections
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 5. Provider Timeout (HTTP 504) Fallback Test
# ---------------------------------------------------------------------------

def test_provider_timeout_fallback(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, name="Timeout Fallback Project")

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", side_effect=HTTPException(status_code=504, detail="Upstream request timed out")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["source"] == "deterministic"
            assert data["ai_status"] == "FALLBACK"
            assert "unavailable" in data["ai_notice"].lower()
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 6. Malformed AI Output Fallback Test
# ---------------------------------------------------------------------------

def test_malformed_ai_response_fallback(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, name="Malformed AI Project")

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", new=AsyncMock(return_value="```json { broken syntax, unclosed [")):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assert data["source"] == "deterministic"
            assert data["ai_status"] == "FALLBACK"
            assert "parsed" in data["ai_notice"].lower()
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 7. Factual Grounding & Anti-Hallucination Test
# ---------------------------------------------------------------------------

def test_factual_grounding_rejects_hallucinations(pm_a_headers):
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, name="Grounding Guard Project")

    # Real In-Progress Task
    task_id = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_id,
        "title": "Real In-Progress Feature",
        "project_id": proj_id,
        "status": "IN_PROGRESS",
    }).execute()

    # Model attempts to hallucinate that the task is completed based on a commit message
    # and attempts to change health score to 25
    hallucinated_ai = json.dumps({
        "health": {
            "score": 25,
            "status": "Disaster",
            "explanation": "I decided to override your score to 25."
        },
        "completed_work": {
            "tasks_count": 5,
            "task_titles": ["Real In-Progress Feature", "Invented Completed Task"],
            "summary": "Completed based on git commit 'feat: complete everything'."
        },
        "github_context": {
            "summary": "Missing GitHub repository is a critical project failure!"
        }
    })

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", new=AsyncMock(return_value=hallucinated_ai)):
            resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()

            # 1. Health score MUST remain deterministic (100)
            assert data["health_score"] == 100
            assert data["sections"]["health"]["score"] == 100

            # 2. Completed tasks count MUST strictly reflect status='COMPLETED' (0 tasks completed)
            assert data["sections"]["completed_work"]["tasks_count"] == 0
            assert "Real In-Progress Feature" not in data["sections"]["completed_work"]["task_titles"]

            # 3. Missing GitHub context is NOT treated as failure
            assert "failure" not in data["sections"]["github_context"]["summary"].lower()
    finally:
        cleanup_project(proj_id)


# ---------------------------------------------------------------------------
# 8. Stale Context Prevention On Project Switching Test
# ---------------------------------------------------------------------------

def test_stale_context_prevention_on_project_switching(pm_a_headers):
    headers, pm_id = pm_a_headers

    proj_1_id = create_project(pm_id, name="Project Alpha")
    proj_2_id = create_project(pm_id, name="Project Beta")

    task_1 = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_1,
        "title": "Alpha Unique Milestone",
        "project_id": proj_1_id,
        "status": "IN_PROGRESS",
    }).execute()

    task_2 = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_2,
        "title": "Beta Unique Architecture",
        "project_id": proj_2_id,
        "status": "IN_PROGRESS",
    }).execute()

    try:
        with patch("app.services.pulse_briefing_service.ask_llm", new=AsyncMock(return_value="{}")):
            # 1. Request Project 1
            resp_1 = client.get(f"/api/v1/dashboard/projects/{proj_1_id}/pulse-briefing", headers=headers)
            assert resp_1.status_code == 200
            data_1 = resp_1.json()
            assert data_1["project_name"] == "Project Alpha"
            assert any(t["title"] == "Alpha Unique Milestone" for t in data_1["verified_facts"]["in_progress_tasks"])
            assert not any(t["title"] == "Beta Unique Architecture" for t in data_1["verified_facts"]["in_progress_tasks"])

            # 2. Request Project 2 immediately (simulating PM switching projects)
            resp_2 = client.get(f"/api/v1/dashboard/projects/{proj_2_id}/pulse-briefing", headers=headers)
            assert resp_2.status_code == 200
            data_2 = resp_2.json()
            assert data_2["project_name"] == "Project Beta"
            assert any(t["title"] == "Beta Unique Architecture" for t in data_2["verified_facts"]["in_progress_tasks"])
            # Zero leakage of Project 1 tasks into Project 2!
            assert not any(t["title"] == "Alpha Unique Milestone" for t in data_2["verified_facts"]["in_progress_tasks"])
    finally:
        cleanup_project(proj_1_id)
        cleanup_project(proj_2_id)


# ---------------------------------------------------------------------------
# 9. Live Gemini Distinction Guard
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_GEMINI_TESTS"),
    reason="Live Gemini tests disabled by default for automated CI reliability. Set RUN_LIVE_GEMINI_TESTS=1 to run."
)
def test_live_gemini_briefing(pm_a_headers):
    """
    Distinguished live test that calls Google Gemini API when RUN_LIVE_GEMINI_TESTS=1 is set.
    """
    headers, pm_id = pm_a_headers
    proj_id = create_project(pm_id, name="Live Gemini Briefing Project")
    try:
        resp = client.get(f"/api/v1/dashboard/projects/{proj_id}/pulse-briefing", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["health_score"] == 100
        assert "health" in data["sections"]
    finally:
        cleanup_project(proj_id)
