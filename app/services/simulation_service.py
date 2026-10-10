import math
import json
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import HTTPException, status
from app.db.supabase_client import get_supabase_client
from app.schemas.user import UserOut
from app.schemas.simulation import (
    DeadlineSimulationRequest,
    DeadlineSimulationResponse,
    SimulationBaseline,
    DeadlineSimulationScenario,
    DeveloperUnavailabilitySimulationRequest,
    UnavailabilitySimulationResponse,
    UnavailabilitySimulationScenario,
)
from app.services.project_service import get_project_by_id_service

def _db():
    return get_supabase_client()

def calculate_working_weeks(start_date: date, end_date: date) -> float:
    if end_date <= start_date:
        return 0.0
    days = (end_date - start_date).days
    return days / 7.0

def _get_project_tasks_and_capacities(project_id: str):
    """Helper to fetch tasks, effort, and developer capacities."""
    try:
        tasks_resp = _db().table("tasks").select("id, title, status, assigned_to, estimated_effort, due_date").eq("project_id", project_id).execute()
        tasks = tasks_resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error fetching tasks: {str(exc)}")

    pending_tasks = [t for t in tasks if (t.get("status") or "").upper() != "COMPLETED"]
    
    total_effort = 0.0
    has_unknown_effort = False
    for t in pending_tasks:
        effort = t.get("estimated_effort")
        if effort is not None:
            total_effort += float(effort)
        else:
            has_unknown_effort = True
            
    has_unknown_capacity = False
    assigned_user_ids = list(set(str(t["assigned_to"]) for t in pending_tasks if t.get("assigned_to")))
    developer_capacities = {}
    if assigned_user_ids:
        try:
            users_resp = _db().table("users").select("id, capacity_hours_per_week, name").in_("id", assigned_user_ids).execute()
            for u in (users_resp.data or []):
                cap = u.get("capacity_hours_per_week")
                if cap is None:
                    has_unknown_capacity = True
                developer_capacities[str(u["id"])] = {
                    "capacity": float(cap) if cap is not None else None,
                    "name": u.get("name", "Unknown")
                }
        except Exception as exc:
            has_unknown_capacity = True
            
    return pending_tasks, total_effort, has_unknown_effort, developer_capacities, has_unknown_capacity

async def _generate_ai_explanation(scenario_type: str, baseline_data: dict, scenario_data: dict) -> str:
    """Generate AI explanation for the simulation."""
    try:
        from app.services.ai_service import ask_llm
        
        prompt = f"""
You are an expert Project Management AI assistant.
Analyze this {scenario_type} simulation scenario and provide a concise explanation (2-4 paragraphs) of the impact.
Do NOT invent tasks, numbers, or dates. Only use the provided data.
Acknowledge missing data if mentioned.
Provide potential recovery options if there is a capacity gap.

Baseline:
{json.dumps(baseline_data, indent=2, default=str)}

Scenario:
{json.dumps(scenario_data, indent=2, default=str)}
"""
        return await ask_llm(prompt)
    except Exception as e:
        print(f"Failed to generate AI explanation: {e}")
        return "AI explanation is temporarily unavailable."

async def simulate_deadline_change(
    project_id: str,
    payload: DeadlineSimulationRequest,
    current_user: UserOut,
) -> DeadlineSimulationResponse:
    project = get_project_by_id_service(project_id, current_user)
    pending_tasks, total_effort, has_unknown_effort, developer_capacities, has_unknown_capacity = _get_project_tasks_and_capacities(project_id)
    
    today = date.today()
    baseline_capacity = 0.0
    
    if project.end_date and project.end_date > today:
        baseline_weeks = calculate_working_weeks(today, project.end_date)
        for dev in developer_capacities.values():
            if dev["capacity"] is not None:
                baseline_capacity += dev["capacity"] * baseline_weeks
    else:
        baseline_capacity = 0.0 

    scenario_capacity = 0.0
    if payload.proposed_deadline > today:
        scenario_weeks = calculate_working_weeks(today, payload.proposed_deadline)
        for dev in developer_capacities.values():
            if dev["capacity"] is not None:
                scenario_capacity += dev["capacity"] * scenario_weeks
            
    schedule_concerns = []
    assumptions_and_limitations = [
        "Developer capacity assumes a uniform distribution over time.",
        "Simulation does not account for task dependencies."
    ]
    
    if has_unknown_effort:
        assumptions_and_limitations.append("Some tasks have unknown estimated_effort; the capacity gap is a partial estimate.")
    if has_unknown_capacity:
        assumptions_and_limitations.append("Some developers have unknown capacity; available capacity may be underreported.")
    
    if payload.proposed_deadline < today:
        schedule_concerns.append("Proposed deadline is in the past. Available capacity is 0.")
    elif scenario_capacity < baseline_capacity:
        schedule_concerns.append("Proposed deadline reduces available capacity for currently assigned developers.")
        
    capacity_gap = None
    if has_unknown_effort or has_unknown_capacity:
        schedule_concerns.append("Schedule assessment is partial due to missing task estimates or developer capacities.")

    if total_effort > scenario_capacity:
        capacity_gap = total_effort - scenario_capacity
        if not has_unknown_effort and not has_unknown_capacity:
            schedule_concerns.append(f"Projected capacity gap of {math.ceil(capacity_gap)} hours detected.")
        else:
            schedule_concerns.append(f"Projected capacity gap of at least {math.ceil(capacity_gap)} hours detected (not counting unknown data).")
    else:
        if not has_unknown_effort and not has_unknown_capacity and len(pending_tasks) > 0:
            schedule_concerns.append("Available capacity covers the estimated work under the stated assumptions.")

    if not schedule_concerns:
        schedule_concerns.append("No immediate schedule concerns detected with this proposal.")

    baseline = SimulationBaseline(
        current_deadline=project.end_date,
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=total_effort if not has_unknown_effort else None, # if any is missing, exact total is unknown
        available_capacity_hours=math.floor(baseline_capacity)
    )
    
    scenario = DeadlineSimulationScenario(
        proposed_deadline=payload.proposed_deadline,
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=total_effort if not has_unknown_effort else None,
        available_capacity_hours=math.floor(scenario_capacity),
        capacity_gap_hours=math.ceil(capacity_gap) if capacity_gap else None,
        schedule_concerns=schedule_concerns
    )
    
    ai_explanation = await _generate_ai_explanation(
        "deadline change",
        baseline.model_dump(),
        scenario.model_dump()
    )
    
    return DeadlineSimulationResponse(
        baseline=baseline,
        scenario=scenario,
        assumptions_and_limitations=assumptions_and_limitations,
        is_simulation=True,
        ai_explanation=ai_explanation
    )

async def simulate_developer_unavailability(
    project_id: str,
    payload: DeveloperUnavailabilitySimulationRequest,
    current_user: UserOut,
) -> UnavailabilitySimulationResponse:
    project = get_project_by_id_service(project_id, current_user)
    pending_tasks, total_effort, has_unknown_effort, developer_capacities, has_unknown_capacity = _get_project_tasks_and_capacities(project_id)
    
    if payload.developer_id not in developer_capacities:
        raise HTTPException(status_code=400, detail="Selected developer is not assigned to any pending tasks in this project.")
        
    dev_capacity_per_week = developer_capacities[payload.developer_id]["capacity"]
    if dev_capacity_per_week is None:
        has_unknown_capacity = True
        dev_capacity_per_week = 0.0 # for calculation purposes
    
    today = date.today()
    
    baseline_capacity = 0.0
    if project.end_date and project.end_date > today:
        baseline_weeks = calculate_working_weeks(today, project.end_date)
        for dev in developer_capacities.values():
            if dev["capacity"] is not None:
                baseline_capacity += dev["capacity"] * baseline_weeks
            
    # Calculate unavailability period
    absence_start = max(payload.start_date, today)
    absence_end = payload.end_date
    if project.end_date and absence_end > project.end_date:
        absence_end = project.end_date
        
    capacity_removed = 0.0
    if absence_end > absence_start:
        absence_weeks = calculate_working_weeks(absence_start, absence_end)
        capacity_removed = absence_weeks * dev_capacity_per_week
        
    scenario_capacity = max(0.0, baseline_capacity - capacity_removed)
    
    affected_tasks = [t for t in pending_tasks if t.get("assigned_to") == payload.developer_id]
    
    schedule_concerns = []
    assumptions_and_limitations = [
        "Developer capacity assumes a uniform distribution over time.",
        "Simulation does not account for task dependencies."
    ]
    
    if has_unknown_effort:
        assumptions_and_limitations.append("Some tasks have unknown estimated_effort; the capacity gap is a partial estimate.")
    if has_unknown_capacity:
        assumptions_and_limitations.append("Some developers have unknown capacity; available capacity may be underreported.")
        
    if capacity_removed > 0:
        schedule_concerns.append(f"Developer absence removes {math.ceil(capacity_removed)} hours of capacity from the project.")
        
    capacity_gap = None
    if has_unknown_effort or has_unknown_capacity:
        schedule_concerns.append("Schedule assessment is partial due to missing task estimates or developer capacities.")

    if total_effort > scenario_capacity:
        capacity_gap = total_effort - scenario_capacity
        if not has_unknown_effort and not has_unknown_capacity:
            schedule_concerns.append(f"Projected overall capacity gap of {math.ceil(capacity_gap)} hours detected.")
        else:
            schedule_concerns.append(f"Projected overall capacity gap of at least {math.ceil(capacity_gap)} hours detected (not counting unknown data).")
    else:
        if not has_unknown_effort and not has_unknown_capacity and len(pending_tasks) > 0:
            schedule_concerns.append("Available capacity covers the estimated work under the stated assumptions.")

    if not schedule_concerns:
        schedule_concerns.append("No immediate schedule concerns detected with this proposal.")

    baseline = SimulationBaseline(
        current_deadline=project.end_date,
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=total_effort if not has_unknown_effort else None,
        available_capacity_hours=math.floor(baseline_capacity)
    )
    
    from app.services.matching_service import get_recommended_team_for_task
    
    replacements = []
    for t in affected_tasks:
        try:
            candidates = get_recommended_team_for_task(t["id"])
            # Filter out the unavailable developer
            candidates = [c for c in candidates if c["developer"]["id"] != payload.developer_id]
            if candidates:
                top_candidate = candidates[0]
                dev_name = top_candidate.get("developer", {}).get("name", "Unknown")
                score = top_candidate.get("match_score", 0)
                replacements.append({
                    "task_id": t["id"],
                    "task_title": t.get("title", "Unknown"),
                    "suggested_developer_name": dev_name,
                    "suggested_developer_id": top_candidate.get("developer", {}).get("id"),
                    "match_score": score
                })
        except Exception as e:
            pass

    scenario = UnavailabilitySimulationScenario(
        developer_id=payload.developer_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        capacity_removed_hours=math.ceil(capacity_removed),
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=total_effort if not has_unknown_effort else None,
        available_capacity_hours=math.floor(scenario_capacity),
        capacity_gap_hours=math.ceil(capacity_gap) if capacity_gap else None,
        affected_tasks=affected_tasks,
        replacements=replacements,
        schedule_concerns=schedule_concerns
    )
    
    ai_explanation = await _generate_ai_explanation(
        "developer unavailability",
        baseline.model_dump(),
        scenario.model_dump()
    )
    
    return UnavailabilitySimulationResponse(
        baseline=baseline,
        scenario=scenario,
        assumptions_and_limitations=assumptions_and_limitations,
        is_simulation=True,
        ai_explanation=ai_explanation
    )
