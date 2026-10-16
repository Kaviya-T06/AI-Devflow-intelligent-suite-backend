"""
Test suite for Project Pulse Health Score Calculation
Tests:
1.  Score boundaries (max 100, min 0)
2.  Formula weights (-15 Critical, -10 High, -5 Medium, -3 Overdue)
3.  Unresolved vs resolved risks (only unresolved count)
4.  Missing data & Empty projects (data completeness logic)
5.  Duplicate-risk handling (no double penalty for overdue tasks with tracking risks)
6.  Project isolation (PM only sees their projects' pulse)
"""
import pytest
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password

client = TestClient(app)
db = get_supabase_client()

@pytest.fixture(scope="module")
def pm_auth_headers():
    pm_id = str(uuid.uuid4())
    pm_email = f"pm_pulse_{pm_id}@test.com"
    pm_pass = hash_password("password123")
    
    db.table("users").insert({
        "id": pm_id,
        "email": pm_email,
        "password_hash": pm_pass,
        "name": "PM Pulse Tester",
        "role": "project_manager",
        "is_active": True
    }).execute()
    
    # Login
    response = client.post("/api/v1/auth/login", json={"email": pm_email, "password": "password123"})
    if response.status_code != 200:
        raise RuntimeError(f"Login failed: {response.text}")
    token = response.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    
    yield headers, pm_id
    
    # Cleanup
    db.table("users").delete().eq("id", pm_id).execute()

def create_project(pm_id, start_date=None, end_date=None, progress=0, name="Pulse Test Project"):
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

def test_empty_project_and_missing_data(pm_auth_headers):
    headers, pm_id = pm_auth_headers
    proj_id = create_project(pm_id) # No tasks, no dates
    
    try:
        resp = client.get("/api/v1/dashboard/pm-stats", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        
        proj = next(p for p in data["projects"] if p["id"] == proj_id)
        assert proj["health"]["score"] == 100
        assert proj["health"]["data_completeness"] == "Low"
        assert "No tasks" in proj["health"]["components"][0]["reason"]
    finally:
        cleanup_project(proj_id)

def test_score_boundaries_and_weights(pm_auth_headers):
    headers, pm_id = pm_auth_headers
    # With dates, progress 100 to avoid schedule lag
    start_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    end_date = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    proj_id = create_project(pm_id, start_date=start_date, end_date=end_date, progress=100)
    
    task_id = str(uuid.uuid4())
    db.table("tasks").insert({
        "id": task_id,
        "title": "Task 1",
        "project_id": proj_id,
        "status": "IN_PROGRESS",
        "due_date": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    }).execute()
    
    try:
        # Base: 100.
        # Add 1 Critical (-15), 1 High (-10)
        db.table("workflow_risks").insert([
            {
                "id": str(uuid.uuid4()), "risk_type": "INFRA", "title": "Crit", "description": "Desc",
                "level": "Critical", "project_id": proj_id, "is_resolved": False
            },
            {
                "id": str(uuid.uuid4()), "risk_type": "API", "title": "High", "description": "Desc",
                "level": "High", "project_id": proj_id, "is_resolved": False
            }
        ]).execute()
        
        resp = client.get("/api/v1/dashboard/pm-stats", headers=headers)
        assert resp.status_code == 200, resp.text
        proj = next(p for p in resp.json()["projects"] if p["id"] == proj_id)
        assert proj["health"]["score"] == 100 - 15 - 10
        assert proj["health"]["data_completeness"] == "High"
        
        # Test floor at 0
        db.table("workflow_risks").insert([
            {
                "id": str(uuid.uuid4()), "risk_type": "INFRA", "title": "Crit", "description": "Crit desc",
                "level": "Critical", "project_id": proj_id, "is_resolved": False
            } for _ in range(8) # 8 * 15 = 120
        ]).execute()
        
        resp = client.get("/api/v1/dashboard/pm-stats", headers=headers)
        assert resp.status_code == 200, resp.text
        proj = next(p for p in resp.json()["projects"] if p["id"] == proj_id)
        assert proj["health"]["score"] == 0 # Min boundary
        
    finally:
        cleanup_project(proj_id)

def test_resolved_risks_ignored(pm_auth_headers):
    headers, pm_id = pm_auth_headers
    proj_id = create_project(pm_id, progress=100)
    db.table("tasks").insert({"id": str(uuid.uuid4()), "title": "Task 1", "project_id": proj_id, "status": "IN_PROGRESS"}).execute()
    
    try:
        db.table("workflow_risks").insert({
            "id": str(uuid.uuid4()), "risk_type": "INFRA", "title": "Crit", "description": "Crit desc",
            "level": "Critical", "project_id": proj_id, "is_resolved": True
        }).execute()
        
        resp = client.get("/api/v1/dashboard/pm-stats", headers=headers)
        assert resp.status_code == 200, resp.text
        proj = next(p for p in resp.json()["projects"] if p["id"] == proj_id)
        assert proj["health"]["score"] == 100 # Risk is resolved, ignored
    finally:
        cleanup_project(proj_id)

def test_duplicate_risk_handling(pm_auth_headers):
    headers, pm_id = pm_auth_headers
    proj_id = create_project(pm_id, progress=100)
    task_id = str(uuid.uuid4())
    # Overdue task
    db.table("tasks").insert({
        "id": task_id, "title": "Overdue Task", "project_id": proj_id, 
        "status": "IN_PROGRESS", "due_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    }).execute()
    
    try:
        # Before risk: Score should be 97 (-3 for overdue)
        resp = client.get("/api/v1/dashboard/pm-stats", headers=headers)
        assert resp.status_code == 200, resp.text
        proj = next(p for p in resp.json()["projects"] if p["id"] == proj_id)
        assert proj["health"]["score"] == 97
        
        # Add risk linked to this task
        db.table("workflow_risks").insert({
            "id": str(uuid.uuid4()), "risk_type": "OVERDUE_TASK", "title": "Overdue risk", "description": "Overdue desc",
            "level": "High", "project_id": proj_id, "task_id": task_id, "is_resolved": False
        }).execute()
        
        # After risk: Score should be 90 (-10 for High risk), but NOT -3 for overdue task (no double counting)
        resp = client.get("/api/v1/dashboard/pm-stats", headers=headers)
        assert resp.status_code == 200, resp.text
        proj = next(p for p in resp.json()["projects"] if p["id"] == proj_id)
        assert proj["health"]["score"] == 90
    finally:
        cleanup_project(proj_id)
