import pytest
from datetime import date, timedelta
from app.schemas.simulation import DeadlineSimulationRequest
from app.schemas.user import UserOut
from app.schemas.auth import RoleEnum
from app.services.simulation_service import simulate_deadline_change, calculate_working_weeks
from fastapi import HTTPException

# Dummy DB objects for mock
class MockUser:
    def __init__(self, id, capacity):
        self.id = id
        self.capacity_hours_per_week = capacity
        
class MockProject:
    def __init__(self, id, pm_id, end_date):
        self.id = id
        self.project_manager_id = pm_id
        self.end_date = end_date
        
class MockTask:
    def __init__(self, id, project_id, status, assigned_to):
        self.id = id
        self.project_id = project_id
        self.status = status
        self.assigned_to = assigned_to

def test_calculate_working_weeks():
    start = date(2026, 10, 1)
    end = date(2026, 10, 15)
    weeks = calculate_working_weeks(start, end)
    assert weeks == 14 / 7.0

@pytest.mark.asyncio
async def test_simulate_earlier_deadline(mocker):
    # Mock current user
    user = UserOut(id="user1", name="Manager", email="m@m.com", role=RoleEnum.PROJECT_MANAGER, is_active=True)
    
    # Mock project_service.get_project_by_id_service
    project = MockProject(id="p1", pm_id="user1", end_date=date.today() + timedelta(days=21))
    mocker.patch("app.services.simulation_service.get_project_by_id_service", return_value=project)
    
    # Mock supabase DB
    mock_db = mocker.MagicMock()
    mocker.patch("app.services.simulation_service._db", return_value=mock_db)
    mocker.patch("app.services.simulation_service._generate_ai_explanation", return_value="Test AI insight")
    
    # Mock tasks (1 remaining)
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "t1", "project_id": "p1", "status": "TODO", "assigned_to": "dev1"}
    ]
    
    # Mock capacity (20 hours/week for dev1)
    mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": "dev1", "capacity_hours_per_week": 20}
    ]

    proposed = date.today() + timedelta(days=7) # 1 week
    payload = DeadlineSimulationRequest(proposed_deadline=proposed)
    
    response = await simulate_deadline_change("p1", payload, user)
    
    # Baseline: 3 weeks * 20 = 60 hours
    assert response.baseline.available_capacity_hours == 60
    assert response.baseline.remaining_tasks == 1
    
    # Scenario: 1 week * 20 = 20 hours
    assert response.scenario.available_capacity_hours == 20
    assert response.scenario.proposed_deadline == proposed
    assert "Proposed deadline reduces available capacity for currently assigned developers." in response.scenario.schedule_concerns
    assert "Some tasks have unknown estimated_effort; the capacity gap is a partial estimate." in response.assumptions_and_limitations

@pytest.mark.asyncio
async def test_simulate_missing_capacity(mocker):
    user = UserOut(id="user1", name="Manager", email="m@m.com", role=RoleEnum.PROJECT_MANAGER, is_active=True)
    project = MockProject(id="p1", pm_id="user1", end_date=date.today() + timedelta(days=21))
    mocker.patch("app.services.simulation_service.get_project_by_id_service", return_value=project)
    
    mock_db = mocker.MagicMock()
    mocker.patch("app.services.simulation_service._db", return_value=mock_db)
    mocker.patch("app.services.simulation_service._generate_ai_explanation", return_value="Test AI insight")
    
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "t1", "project_id": "p1", "status": "TODO", "assigned_to": "dev1"}
    ]
    
    # DB exception when fetching users, defaults to 0 capacity
    mock_db.table.return_value.select.return_value.in_.return_value.execute.side_effect = Exception("DB error")

    payload = DeadlineSimulationRequest(proposed_deadline=date.today() + timedelta(days=7))
    response = await simulate_deadline_change("p1", payload, user)
    
    assert response.baseline.available_capacity_hours == 0
    assert response.scenario.available_capacity_hours == 0

@pytest.mark.asyncio
async def test_cross_project_access_denial(mocker):
    user = UserOut(id="user2", name="Another Manager", email="m2@m.com", role=RoleEnum.PROJECT_MANAGER, is_active=True)
    # the project_service should handle the HTTP exception, so we just mock it raising the exception
    mocker.patch("app.services.simulation_service.get_project_by_id_service", side_effect=HTTPException(status_code=403))
    
    payload = DeadlineSimulationRequest(proposed_deadline=date.today() + timedelta(days=7))
    
    with pytest.raises(HTTPException) as exc:
        await simulate_deadline_change("p1", payload, user)
        
    assert exc.value.status_code == 403

def test_invalid_input_handled_by_pydantic():
    # Will be tested automatically by FastAPI but we can ensure model validation
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DeadlineSimulationRequest(proposed_deadline="invalid date")

def test_count_working_days():
    from app.services.simulation_service import count_working_days
    # Friday Oct 16, 2026 to Saturday Oct 31, 2026
    # Oct 19-23 (5 days) + Oct 26-30 (5 days) = 10 working days
    start = date(2026, 10, 16)
    end = date(2026, 10, 31)
    assert count_working_days(start, end) == 10
    
    # Same day = 0 working days
    assert count_working_days(start, start) == 0

@pytest.mark.asyncio
async def test_compare_oct31_current_with_oct16_proposed(mocker):
    """
    Requirement 7 test:
    Current deadline: October 31, 2026
    Proposed deadline: October 16, 2026
    Verify schedule concerns based on effort, capacity, remaining tasks, and working days.
    """
    user = UserOut(id="user1", name="Manager", email="m@m.com", role=RoleEnum.PROJECT_MANAGER, is_active=True)
    # Today is date.today(), mock baseline end_date = 2026-10-31
    cur_deadline = date(2026, 10, 31)
    proposed = date(2026, 10, 16)
    project = MockProject(id="p_emp", pm_id="user1", end_date=cur_deadline)
    mocker.patch("app.services.simulation_service.get_project_by_id_service", return_value=project)

    mock_db = mocker.MagicMock()
    mocker.patch("app.services.simulation_service._db", return_value=mock_db)
    mocker.patch("app.services.simulation_service._generate_ai_explanation", return_value="AI analysis")

    # 3 tasks with 40h each = 120h total effort
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "t1", "project_id": "p_emp", "status": "IN_PROGRESS", "assigned_to": "dev1", "estimated_hours": 40.0},
        {"id": "t2", "project_id": "p_emp", "status": "TODO", "assigned_to": "dev2", "estimated_hours": 40.0},
        {"id": "t3", "project_id": "p_emp", "status": "TODO", "assigned_to": "dev1", "estimated_hours": 40.0},
        {"id": "t4", "project_id": "p_emp", "status": "COMPLETED", "assigned_to": "dev1", "estimated_hours": 20.0},
    ]

    # dev1 and dev2 both have 40 hrs/week
    mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": "dev1", "full_name": "Dev One", "capacity_hours_per_week": 40.0},
        {"id": "dev2", "full_name": "Dev Two", "capacity_hours_per_week": 40.0},
    ]

    payload = DeadlineSimulationRequest(proposed_deadline=proposed)
    resp = await simulate_deadline_change("p_emp", payload, user)

    # 3 incomplete tasks
    assert resp.baseline.remaining_tasks == 3
    assert resp.baseline.remaining_estimated_effort_hours == 120.0
    assert resp.baseline.tasks_with_estimates == 3
    assert resp.baseline.tasks_missing_estimates == 0

    # Proposed deadline is Oct 16 (today) -> available capacity is 0
    assert resp.scenario.available_capacity_hours == 0
    # Capacity gap: 120h effort - 0h capacity = 120h gap
    assert resp.scenario.capacity_gap_hours == 120
    assert resp.scenario.capacity_gap_status == "calculated"
    # Schedule concern is detected
    assert any("Proposed deadline" in c for c in resp.scenario.schedule_concerns)
    assert any("gap" in c.lower() for c in resp.scenario.schedule_concerns)

@pytest.mark.asyncio
async def test_partial_effort_calculation(mocker):
    """
    Requirement 4 & 8: If some tasks lack estimates, calculate what can be calculated
    and clearly report the count of tasks missing estimates.
    """
    user = UserOut(id="user1", name="Manager", email="m@m.com", role=RoleEnum.PROJECT_MANAGER, is_active=True)
    project = MockProject(id="p1", pm_id="user1", end_date=date.today() + timedelta(days=21))
    mocker.patch("app.services.simulation_service.get_project_by_id_service", return_value=project)

    mock_db = mocker.MagicMock()
    mocker.patch("app.services.simulation_service._db", return_value=mock_db)
    mocker.patch("app.services.simulation_service._generate_ai_explanation", return_value="AI analysis")

    # 2 tasks: 1 with 30 hrs estimate, 1 with None
    mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "t1", "project_id": "p1", "status": "IN_PROGRESS", "assigned_to": "dev1", "estimated_hours": 30.0},
        {"id": "t2", "project_id": "p1", "status": "TODO", "assigned_to": "dev1", "estimated_hours": None},
    ]
    mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
        {"id": "dev1", "full_name": "Dev One", "capacity_hours_per_week": 40.0},
    ]

    payload = DeadlineSimulationRequest(proposed_deadline=date.today() + timedelta(days=14))
    resp = await simulate_deadline_change("p1", payload, user)

    # Remaining effort is 30.0 from known tasks
    assert resp.baseline.remaining_estimated_effort_hours == 30.0
    assert resp.baseline.tasks_with_estimates == 1
    assert resp.baseline.tasks_missing_estimates == 1
    assert any("1 of 2 remaining tasks lack effort estimates." in item for item in resp.scenario.missing_data_items)

