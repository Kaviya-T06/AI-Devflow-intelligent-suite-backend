"""
Test Suite: Project Detail Filtering for Tasks and History.
Verifies:
1. When a developer views a specific project, only tasks belonging to that project are returned.
2. Developer requesting tasks for an unassigned project receives 403 Forbidden.
3. When a developer views project history, only history records belonging to that project are returned.
4. Developer requesting history for an unassigned project receives 403 Forbidden.
5. Project isolation: tasks and activity from other projects are strictly excluded.
"""
import os
import sys
import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from app.db.supabase_client import get_supabase_client
from app.core.security import hash_password, create_access_token
from app.services.activity_service import log_activity

client = TestClient(app)
db = get_supabase_client()


def cleanup(table: str, ids: list):
    for item_id in ids:
        try:
            db.table(table).delete().eq("id", item_id).execute()
        except Exception:
            pass


@pytest.fixture(scope="module")
def setup_test_data():
    suffix = str(uuid.uuid4())[:8]
    password = "TestPassword123!"

    dev1_id = str(uuid.uuid4())
    dev2_id = str(uuid.uuid4())
    pm_id = str(uuid.uuid4())

    proj_smart_campus_id = str(uuid.uuid4())
    proj_hospital_crm_id = str(uuid.uuid4())

    task_sc_1_id = str(uuid.uuid4())
    task_sc_2_id = str(uuid.uuid4())
    task_hc_1_id = str(uuid.uuid4())

    activity_ids = []

    # 1. Users
    db.table("users").insert([
        {
            "id": dev1_id,
            "name": f"Developer One {suffix}",
            "email": f"dev1_{suffix}@test.com",
            "password_hash": hash_password(password),
            "role": "developer",
            "is_active": True,
        },
        {
            "id": dev2_id,
            "name": f"Developer Two {suffix}",
            "email": f"dev2_{suffix}@test.com",
            "password_hash": hash_password(password),
            "role": "developer",
            "is_active": True,
        },
        {
            "id": pm_id,
            "name": f"PM {suffix}",
            "email": f"pm_{suffix}@test.com",
            "password_hash": hash_password(password),
            "role": "project_manager",
            "is_active": True,
        },
    ]).execute()

    # 2. Projects
    db.table("projects").insert([
        {
            "id": proj_smart_campus_id,
            "name": f"Smart Campus Resource Booking System {suffix}",
            "description": "Smart campus portal",
            "project_manager_id": pm_id,
            "status": "active",
            "progress": 50,
        },
        {
            "id": proj_hospital_crm_id,
            "name": f"Hospital CRM System {suffix}",
            "description": "Hospital portal",
            "project_manager_id": pm_id,
            "status": "active",
            "progress": 20,
        },
    ]).execute()

    # 3. Tasks
    # dev1 is assigned to task_sc_1 (Smart Campus) and task_hc_1 (Hospital CRM)
    # dev2 is assigned to task_sc_2 (Smart Campus)
    db.table("tasks").insert([
        {
            "id": task_sc_1_id,
            "title": "Smart Campus - Room Reservation API",
            "project_id": proj_smart_campus_id,
            "assigned_to": dev1_id,
            "status": "COMPLETED",
            "priority": "HIGH",
        },
        {
            "id": task_sc_2_id,
            "title": "Smart Campus - QR Code Scanner",
            "project_id": proj_smart_campus_id,
            "assigned_to": dev2_id,
            "status": "IN_PROGRESS",
            "priority": "MEDIUM",
        },
        {
            "id": task_hc_1_id,
            "title": "Hospital CRM - Patient Intake Form",
            "project_id": proj_hospital_crm_id,
            "assigned_to": dev1_id,
            "status": "TODO",
            "priority": "LOW",
        },
    ]).execute()

    # 4. Activity Logs
    # Activity for Smart Campus
    log_activity(
        user_id=dev1_id,
        action="TASK_COMPLETED",
        entity_type="task",
        entity_id=task_sc_1_id,
        project_id=proj_smart_campus_id,
        description="Completed Room Reservation API for Smart Campus",
    )
    # Activity for Hospital CRM
    log_activity(
        user_id=dev1_id,
        action="TASK_CREATED",
        entity_type="task",
        entity_id=task_hc_1_id,
        project_id=proj_hospital_crm_id,
        description="Created Patient Intake Form for Hospital CRM",
    )

    dev1_token = create_access_token({"sub": dev1_id, "role": "developer"})
    dev2_token = create_access_token({"sub": dev2_id, "role": "developer"})

    yield {
        "dev1_id": dev1_id,
        "dev2_id": dev2_id,
        "pm_id": pm_id,
        "proj_smart_campus_id": proj_smart_campus_id,
        "proj_hospital_crm_id": proj_hospital_crm_id,
        "task_sc_1_id": task_sc_1_id,
        "task_sc_2_id": task_sc_2_id,
        "task_hc_1_id": task_hc_1_id,
        "dev1_headers": {"Authorization": f"Bearer {dev1_token}"},
        "dev2_headers": {"Authorization": f"Bearer {dev2_token}"},
    }

    # Cleanup
    cleanup("tasks", [task_sc_1_id, task_sc_2_id, task_hc_1_id])
    cleanup("projects", [proj_smart_campus_id, proj_hospital_crm_id])
    cleanup("users", [dev1_id, dev2_id, pm_id])


def test_developer_project_tasks_filtering(setup_test_data):
    """
    Developer querying /tasks?project_id=<smart_campus_id> receives only tasks
    for Smart Campus, not Hospital CRM.
    """
    data = setup_test_data
    res = client.get(
        f"/api/v1/tasks?project_id={data['proj_smart_campus_id']}",
        headers=data["dev1_headers"],
    )
    assert res.status_code == 200, res.text
    tasks = res.json()
    assert len(tasks) >= 2
    task_ids = [t["id"] for t in tasks]
    assert data["task_sc_1_id"] in task_ids
    assert data["task_sc_2_id"] in task_ids
    # Task from other project must not be included
    assert data["task_hc_1_id"] not in task_ids
    for t in tasks:
        assert t["project_id"] == data["proj_smart_campus_id"]


def test_developer_unassigned_project_tasks_forbidden(setup_test_data):
    """
    Dev2 is not assigned to Hospital CRM, so querying /tasks?project_id=<hospital_crm>
    must return 403 Forbidden.
    """
    data = setup_test_data
    res = client.get(
        f"/api/v1/tasks?project_id={data['proj_hospital_crm_id']}",
        headers=data["dev2_headers"],
    )
    assert res.status_code == 403, res.text


def test_developer_project_history_filtering(setup_test_data):
    """
    Developer querying /projects/<smart_campus_id>/history receives only history
    belonging to Smart Campus.
    """
    data = setup_test_data
    res = client.get(
        f"/api/v1/projects/{data['proj_smart_campus_id']}/history",
        headers=data["dev1_headers"],
    )
    assert res.status_code == 200, res.text
    history = res.json()
    for item in history:
        assert item.get("project_id") == data["proj_smart_campus_id"]
        # Must not contain Hospital CRM text
        assert "Hospital CRM" not in (item.get("description") or "")


def test_developer_unassigned_project_history_forbidden(setup_test_data):
    """
    Dev2 has no tasks in Hospital CRM, so querying its history must return 403 Forbidden.
    """
    data = setup_test_data
    res = client.get(
        f"/api/v1/projects/{data['proj_hospital_crm_id']}/history",
        headers=data["dev2_headers"],
    )
    assert res.status_code == 403, res.text


def test_project_tasks_dedicated_route(setup_test_data):
    """
    GET /projects/{project_id}/tasks also returns project-filtered tasks.
    """
    data = setup_test_data
    res = client.get(
        f"/api/v1/projects/{data['proj_smart_campus_id']}/tasks",
        headers=data["dev1_headers"],
    )
    assert res.status_code == 200, res.text
    tasks = res.json()
    for t in tasks:
        assert t["project_id"] == data["proj_smart_campus_id"]
