"""
Live E2E Verification Script for Smart Allocation Task Synchronization.
Interacts directly with the running FastAPI backend (http://localhost:8000)
and the live Supabase development database.
"""
import urllib.request
import urllib.error
import json
import sys

from app.core.security import create_access_token

BASE_URL = "http://localhost:8000/api/v1"

# Known live DB test accounts and projects
PM_USER_ID = "ab60cba7-cdff-4f7f-a24b-8f59d1e6023f"  # Rahul Kumar (PM)
OTHER_PM_PROJECT_ID = "9706106d-c1e5-4e6d-afeb-b5da43abfd9e"  # Project managed by a different PM

def make_request(url: str, method: str = "GET", payload: dict = None, token: str = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            return status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, json.loads(body) if body else {"detail": e.reason}

def run_e2e_verification():
    print("=" * 70)
    print("STARTING LIVE END-TO-END VERIFICATION (API + LIVE DATABASE)")
    print("=" * 70)

    # 1. Generate JWT Token for PM User
    pm_token = create_access_token({"sub": PM_USER_ID})
    print(f"[OK] Created authentication JWT for PM User (ID: {PM_USER_ID})")

    # 2. Fetch PM Managed Projects
    status, projects = make_request(f"{BASE_URL}/projects", token=pm_token)
    assert status == 200, f"Failed to fetch projects: {projects}"
    assert len(projects) > 0, "No projects found for PM"
    target_project = projects[0]
    project_id = target_project["id"]
    print(f"[OK] Fetched PM managed project: '{target_project['name']}' (ID: {project_id})")

    # 3. Create Unassigned Task (Team Tasks workflow)
    task_payload = {
        "title": "E2E Live Task - Smart Allocation Sync",
        "description": "Verification of task sync between Team Tasks and Smart Allocation",
        "project_id": project_id,
        "assigned_to": None,
        "status": "TODO",
        "priority": "HIGH",
        "due_date": "2026-12-31",
        "required_skills": ["Python", "FastAPI"],
        "min_experience_years": 2,
    }
    status, created_task = make_request(f"{BASE_URL}/tasks", method="POST", payload=task_payload, token=pm_token)
    assert status == 201, f"Failed to create task: {created_task}"
    task_id = created_task["id"]
    print(f"[OK] Created unassigned task via POST /tasks: '{created_task['title']}' (ID: {task_id})")

    try:
        # 4. Task Synchronization in Smart Allocation
        status, task_list = make_request(f"{BASE_URL}/tasks?project_id={project_id}", token=pm_token)
        assert status == 200, f"Failed to list tasks: {task_list}"
        found_task = next((t for t in task_list if t["id"] == task_id), None)
        assert found_task is not None, "Created task not found in GET /tasks response"
        assert found_task["assigned_to"] is None, "Task should be unassigned"
        
        # Verify Smart Allocation frontend filter logic (unassigned tasks)
        unassigned_filtered = [t for t in task_list if not t.get("assigned_to")]
        assert any(t["id"] == task_id for t in unassigned_filtered), "Unassigned task excluded from Smart Allocation filter!"
        print("[OK] Verified task synchronization: Task appears in GET /tasks and passes Smart Allocation filter")

        # 5. Fetch AI Developer Recommendations
        status, recommendations = make_request(f"{BASE_URL}/tasks/{task_id}/recommendations", token=pm_token)
        assert status == 200, f"Failed to fetch recommendations: {recommendations}"
        print(f"[OK] Fetched recommendations for task (Candidates found: {len(recommendations)})")
        if recommendations:
            top_candidate = recommendations[0]
            dev_name = top_candidate.get("developer", {}).get("full_name") or top_candidate.get("developer_name") or "Developer"
            print(f"  -> Top Candidate: {dev_name} (Score: {top_candidate.get('match_score')}%)")

        # 6. Fetch Assignable Users for Project
        status, assignable_users = make_request(f"{BASE_URL}/dashboard/pm-users", token=pm_token)
        assert status == 200, f"Failed to fetch assignable users: {assignable_users}"
        assert len(assignable_users) > 0, "No assignable users returned"
        target_dev = assignable_users[0]
        dev_id = target_dev["id"]
        print(f"[OK] Fetched assignable candidate: '{target_dev['name']}' (ID: {dev_id})")

        # 7. Perform Manager-Approved Task Assignment
        assign_payload = {"assigned_to": dev_id}
        status, updated_task = make_request(f"{BASE_URL}/tasks/{task_id}", method="PATCH", payload=assign_payload, token=pm_token)
        assert status == 200, f"Failed to update task assignment: {updated_task}"
        assert updated_task["assigned_to"] == dev_id, "Assignee ID mismatch"
        print(f"[OK] Approved assignment via PATCH /tasks/{task_id}: Assigned to '{target_dev['name']}'")

        # 8. Verify Persistence & Disappearance from Unassigned List
        status, refreshed_task_list = make_request(f"{BASE_URL}/tasks?project_id={project_id}", token=pm_token)
        assert status == 200, f"Failed to refresh tasks: {refreshed_task_list}"
        
        persisted_task = next((t for t in refreshed_task_list if t["id"] == task_id), None)
        assert persisted_task is not None, "Task missing after page reload / refetch"
        assert persisted_task["assigned_to"] == dev_id, "Assignment did not persist in live database"

        # Apply Smart Allocation unassigned filter
        unassigned_after = [t for t in refreshed_task_list if not t.get("assigned_to")]
        assert not any(t["id"] == task_id for t in unassigned_after), "Assigned task still appears in unassigned allocation list!"
        print("[OK] Verified persistence & list disappearance: Task has assignee in DB and is removed from unassigned allocation list")

        # 9. Verify Project Isolation & RBAC Authorization
        status, forbidden_resp = make_request(f"{BASE_URL}/tasks?project_id={OTHER_PM_PROJECT_ID}", token=pm_token)
        assert status == 403, f"Project isolation failed! Expected 403 Forbidden, got {status}"
        print("[OK] Verified project isolation: Accessing another manager's project correctly returned 403 Forbidden")

        # 10. Verify Error States
        status, not_found_resp = make_request(f"{BASE_URL}/tasks/00000000-0000-0000-0000-000000000000", token=pm_token)
        assert status == 404, f"Expected 404 for non-existent task, got {status}"
        print("[OK] Verified error handling: Non-existent task ID returned 404 Not Found")

    finally:
        # Cleanup temporary live test task
        status, _ = make_request(f"{BASE_URL}/tasks/{task_id}", method="DELETE", token=pm_token)
        print(f"[OK] Cleaned up live test task (DELETE status: {status})")

    print("=" * 70)
    print("ALL LIVE END-TO-END VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_e2e_verification()
