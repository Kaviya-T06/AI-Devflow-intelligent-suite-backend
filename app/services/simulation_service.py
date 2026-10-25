import math
import json
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple, Union

from fastapi import HTTPException, status
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.user import UserOut
from app.schemas.simulation import (
    DeadlineSimulationRequest,
    DeadlineSimulationResponse,
    SimulationBaseline,
    DeadlineSimulationScenario,
    DeveloperUnavailabilitySimulationRequest,
    UnavailabilitySimulationResponse,
    UnavailabilitySimulationScenario,
    DeveloperCapacityInfo,
    ProjectSimulatorData,
)
from app.services.project_service import get_project_by_id_service
from app.services.activity_service import log_activity

def _db():
    return get_supabase_client()

def count_working_days(start_date: Union[date, str], end_date: Union[date, str]) -> int:
    """
    Count business working days (Monday-Friday) between start_date and end_date.
    If end_date <= start_date, returns 0.
    Iterates from start_date + 1 day to end_date (inclusive).
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    if end_date <= start_date:
        return 0
    cur = start_date + timedelta(days=1)
    working_days = 0
    while cur <= end_date:
        if cur.weekday() < 5:  # Mon (0) to Fri (4)
            working_days += 1
        cur += timedelta(days=1)
    return working_days

def calculate_working_weeks(start_date: Union[date, str], end_date: Union[date, str]) -> float:
    """
    Calculate working weeks between start_date and end_date.
    Returns calendar days / 7.0 for smooth scaling.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    if end_date <= start_date:
        return 0.0
    days = (end_date - start_date).days
    return days / 7.0

def _get_project_tasks_and_capacities(project_id: str):
    """
    Helper to fetch tasks, effort estimates, and developer capacities from real database records.
    Queries both profiles and users tables to reliably determine assigned developer capacities.
    Does NOT assume developer capacity is zero when missing; flags it explicitly.
    """
    try:
        tasks_resp = _db().table("tasks").select("*").eq("project_id", project_id).execute()
        tasks = tasks_resp.data or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error fetching tasks: {str(exc)}")

    pending_tasks = [t for t in tasks if (t.get("status") or "").upper() != "COMPLETED"]
    
    total_effort = 0.0
    tasks_with_estimates = 0
    tasks_missing_estimates = 0
    
    for t in pending_tasks:
        effort = t.get("estimated_hours")
        if effort is None:
            effort = t.get("estimated_effort")
        if effort is not None:
            try:
                numeric_effort = float(effort)
                if numeric_effort >= 0:
                    total_effort += numeric_effort
                    tasks_with_estimates += 1
                    t["effective_estimated_hours"] = numeric_effort
                else:
                    tasks_missing_estimates += 1
                    t["effective_estimated_hours"] = None
            except (ValueError, TypeError):
                tasks_missing_estimates += 1
                t["effective_estimated_hours"] = None
        else:
            tasks_missing_estimates += 1
            t["effective_estimated_hours"] = None

    assigned_user_ids = list(set(str(t["assigned_to"]) for t in pending_tasks if t.get("assigned_to")))
    developer_capacities: Dict[str, dict] = {}
    developers_with_capacity = 0
    developers_missing_capacity = 0

    if assigned_user_ids:
        profiles_by_id = {}
        try:
            prof_resp = _db().table("profiles").select("id, full_name, email, role, capacity_hours_per_week").in_("id", assigned_user_ids).execute()
            for p in (prof_resp.data or []):
                profiles_by_id[str(p["id"])] = p
        except Exception as exc:
            print(f"Notice: failed querying profiles for capacity: {exc}")

        users_by_id = {}
        try:
            users_resp = _db().table("users").select("*").in_("id", assigned_user_ids).execute()
            for u in (users_resp.data or []):
                users_by_id[str(u["id"])] = u
        except Exception as exc:
            print(f"Notice: failed querying users: {exc}")

        for uid in assigned_user_ids:
            prof = profiles_by_id.get(uid, {})
            usr = users_by_id.get(uid, {})
            name = prof.get("full_name") or usr.get("name") or "Unknown Developer"
            email = prof.get("email") or usr.get("email")
            
            cap = prof.get("capacity_hours_per_week")
            if cap is None and usr.get("capacity_hours_per_week") is not None:
                cap = usr.get("capacity_hours_per_week")

            assigned_count = sum(1 for t in pending_tasks if str(t.get("assigned_to")) == uid)

            if cap is not None:
                try:
                    cap_val = float(cap)
                    developers_with_capacity += 1
                    has_cap = True
                except (ValueError, TypeError):
                    cap_val = None
                    developers_missing_capacity += 1
                    has_cap = False
            else:
                cap_val = None
                developers_missing_capacity += 1
                has_cap = False

            developer_capacities[uid] = {
                "id": uid,
                "name": name,
                "email": email,
                "capacity": cap_val,
                "has_capacity": has_cap,
                "assigned_tasks_count": assigned_count
            }

    has_unknown_effort = tasks_missing_estimates > 0
    has_unknown_capacity = developers_missing_capacity > 0

    return (
        pending_tasks,
        total_effort,
        tasks_with_estimates,
        tasks_missing_estimates,
        has_unknown_effort,
        developer_capacities,
        developers_with_capacity,
        developers_missing_capacity,
        has_unknown_capacity
    )

def _synthesize_simulation_narrative(scenario_type: str, baseline_data: dict, scenario_data: dict) -> str:
    """Fast, deterministic narrative based on verified simulation metrics."""
    b_days = baseline_data.get("working_days", 0)
    s_days = scenario_data.get("working_days", 0)
    gap = scenario_data.get("capacity_gap_hours")
    gap_status = scenario_data.get("capacity_gap_status")
    s_cap = scenario_data.get("available_capacity_hours", 0)
    rem_effort = scenario_data.get("remaining_estimated_effort_hours")

    sentences = []
    if scenario_type == "deadline change":
        if s_days < b_days:
            diff = b_days - s_days
            sentences.append(f"The proposed deadline compresses the timeline by {diff} working day(s) (from {b_days} down to {s_days} days).")
        elif s_days > b_days:
            diff = s_days - b_days
            sentences.append(f"The proposed deadline extends the timeline by {diff} working day(s) (from {b_days} up to {s_days} days).")
        else:
            sentences.append(f"The proposed deadline maintains {s_days} available working days.")
    else:
        removed = scenario_data.get("capacity_removed_hours", 0)
        sentences.append(f"Simulating this developer unavailability temporarily removes approximately {removed} hours of developer capacity.")

    if gap is not None:
        if gap < 0:
            shortfall = abs(gap)
            sentences.append(f"This results in a projected capacity shortfall of {shortfall:.1f} hours, where available team capacity ({s_cap} hrs) is insufficient for estimated effort ({rem_effort or 'unknown'} hrs).")
            sentences.append("Recommended recovery options include reallocating priority tasks, adjusting sprint scope, or adding developer capacity.")
        elif gap == 0:
            sentences.append(f"Available team capacity ({s_cap} hrs) exactly matches the remaining estimated effort.")
        else:
            sentences.append(f"Available team capacity ({s_cap} hrs) provides a positive capacity buffer of {gap:.1f} hours over the verified remaining effort ({rem_effort} hrs), subject to scheduling constraints.")
    elif gap_status == "unavailable":
        sentences.append("Some remaining tasks lack effort estimates. Add estimates to evaluate deadline feasibility accurately.")
        sentences.append("Recommendation: Update remaining task estimates and developer weekly capacities to enable full gap analysis.")
    else:
        sentences.append(f"Available team capacity ({s_cap} hrs) is sufficient to cover the verified remaining effort under current assumptions.")

    return " ".join(sentences)


async def _generate_ai_explanation(scenario_type: str, baseline_data: dict, scenario_data: dict) -> str:
    """Generate AI explanation for the simulation with fast timeout and instant fallback."""
    try:
        from app.services.ai_service import ask_llm
        
        prompt = f"""
You are an expert Project Management AI assistant.
In 2-3 concise sentences, summarize the impact of this {scenario_type} simulation:
Baseline: {json.dumps(baseline_data, default=str)}
Scenario: {json.dumps(scenario_data, default=str)}
State the working days, capacity gap, and 1 actionable recovery option.
"""
        return await asyncio.wait_for(ask_llm(prompt), timeout=3.5)
    except Exception:
        return _synthesize_simulation_narrative(scenario_type, baseline_data, scenario_data)

async def simulate_deadline_change(
    project_id: str,
    payload: DeadlineSimulationRequest,
    current_user: UserOut,
) -> DeadlineSimulationResponse:
    project = get_project_by_id_service(project_id, current_user)
    (
        pending_tasks,
        total_effort,
        tasks_with_estimates,
        tasks_missing_estimates,
        has_unknown_effort,
        developer_capacities,
        developers_with_capacity,
        developers_missing_capacity,
        has_unknown_capacity
    ) = _get_project_tasks_and_capacities(project_id)
    
    today = date.today()

    # Working days & weeks calculation
    baseline_days = count_working_days(today, project.end_date) if project.end_date else 0
    baseline_weeks = calculate_working_weeks(today, project.end_date) if project.end_date else 0.0

    scenario_days = count_working_days(today, payload.proposed_deadline)
    scenario_weeks = calculate_working_weeks(today, payload.proposed_deadline)

    baseline_capacity = 0.0
    if project.end_date and project.end_date > today:
        for dev in developer_capacities.values():
            if dev["capacity"] is not None:
                baseline_capacity += dev["capacity"] * baseline_weeks

    scenario_capacity = 0.0
    if payload.proposed_deadline > today:
        for dev in developer_capacities.values():
            if dev["capacity"] is not None:
                scenario_capacity += dev["capacity"] * scenario_weeks

    schedule_concerns: List[str] = []
    missing_data_items: List[str] = []
    assumptions_and_limitations = [
        "Developer capacity assumes a uniform weekly distribution over business working days.",
        "Simulation does not account for complex task dependencies or blocker relationships."
    ]

    # Data completeness tracking
    if tasks_missing_estimates > 0:
        missing_data_items.append(
            f"{tasks_missing_estimates} of {len(pending_tasks)} remaining tasks lack effort estimates."
        )
        assumptions_and_limitations.append("Some tasks have unknown estimated_effort; the capacity gap is a partial estimate.")
        assumptions_and_limitations.append(
            f"{tasks_missing_estimates} remaining task(s) lack estimated_hours; capacity gap and effort calculations are based on known tasks."
        )

    if developers_missing_capacity > 0:
        missing_devs = [d["name"] for d in developer_capacities.values() if not d["has_capacity"]]
        missing_data_items.append(
            f"Assigned developer(s) lacking weekly capacity records: {', '.join(missing_devs)}."
        )
        assumptions_and_limitations.append("Some developers have unknown capacity; available capacity may be underreported.")
        assumptions_and_limitations.append(
            f"{developers_missing_capacity} developer(s) lack weekly capacity data; their capacity is not assumed to be zero."
        )

    # Schedule comparison: Current vs Proposed Deadline
    if payload.proposed_deadline < today:
        schedule_concerns.append(
            f"Proposed deadline ({payload.proposed_deadline}) is in the past. Available working days and capacity are 0."
        )
    elif payload.proposed_deadline == today:
        schedule_concerns.append(
            f"Proposed deadline ({payload.proposed_deadline}) is today, leaving 0 working days to complete {len(pending_tasks)} remaining task(s)."
        )
    elif scenario_capacity < baseline_capacity:
        schedule_concerns.append("Proposed deadline reduces available capacity for currently assigned developers.")
        if project.end_date and payload.proposed_deadline < project.end_date:
            days_diff = baseline_days - scenario_days
            cap_diff = max(0.0, baseline_capacity - scenario_capacity)
            schedule_concerns.append(
                f"Proposed deadline ({payload.proposed_deadline}) compresses schedule by {days_diff} working day(s) "
                f"compared to current deadline ({project.end_date}), reducing available capacity by {math.ceil(cap_diff)} hours."
            )
    elif project.end_date and payload.proposed_deadline > project.end_date:
        days_gained = scenario_days - baseline_days
        cap_gained = max(0.0, scenario_capacity - baseline_capacity)
        schedule_concerns.append(
            f"Proposed deadline extends schedule by {days_gained} working day(s), adding {math.ceil(cap_gained)} hours of capacity."
        )

    # Capacity Gap calculation: Capacity Gap = Available Capacity - Estimated Effort Required
    capacity_gap = None
    capacity_gap_status = "calculated"

    if tasks_missing_estimates > 0:
        capacity_gap = None
        capacity_gap_status = "unavailable"
        schedule_concerns.append(
            f"Some remaining tasks lack effort estimates ({tasks_missing_estimates} of {len(pending_tasks)} unestimated). Add estimates to evaluate deadline feasibility accurately."
        )
    elif len(developer_capacities) > 0 and developers_with_capacity == 0:
        capacity_gap = None
        capacity_gap_status = "unavailable"
        schedule_concerns.append("Capacity gap cannot be calculated: assigned developers lack weekly capacity data.")
    else:
        # All required estimates and capacities exist
        # Capacity Gap = Available Capacity - Estimated Effort Required
        capacity_gap = scenario_capacity - total_effort
        capacity_gap_status = "calculated"

        if capacity_gap > 0:
            schedule_concerns.append(
                f"Sufficient capacity exists: available capacity ({scenario_capacity:.1f} hrs across {scenario_days} working days) exceeds estimated effort ({total_effort:.1f} hrs) by {capacity_gap:.1f} hrs, subject to scheduling constraints."
            )
        elif capacity_gap == 0:
            schedule_concerns.append(
                f"Capacity exactly matches estimated effort ({total_effort:.1f} hrs across {scenario_days} working days)."
            )
        else: # capacity_gap < 0
            shortfall = abs(capacity_gap)
            schedule_concerns.append(
                f"Projected capacity shortfall of {shortfall:.1f} hours detected: "
                f"estimated effort ({total_effort:.1f} hrs) exceeds available capacity ({scenario_capacity:.1f} hrs)."
            )

    if not schedule_concerns:
        schedule_concerns.append("No immediate schedule concerns detected with this proposal.")

    baseline = SimulationBaseline(
        current_deadline=project.end_date,
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=round(total_effort, 1) if tasks_with_estimates > 0 else None,
        available_capacity_hours=math.floor(baseline_capacity),
        working_days=baseline_days,
        working_weeks=round(baseline_weeks, 2),
        tasks_with_estimates=tasks_with_estimates,
        tasks_missing_estimates=tasks_missing_estimates,
        developers_with_capacity=developers_with_capacity,
        developers_missing_capacity=developers_missing_capacity,
    )
    
    scenario = DeadlineSimulationScenario(
        proposed_deadline=payload.proposed_deadline,
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=round(total_effort, 1) if tasks_with_estimates > 0 else None,
        available_capacity_hours=math.floor(scenario_capacity),
        capacity_gap_hours=math.ceil(capacity_gap) if capacity_gap is not None else None,
        capacity_gap_status=capacity_gap_status,
        working_days=scenario_days,
        working_weeks=round(scenario_weeks, 2),
        tasks_with_estimates=tasks_with_estimates,
        tasks_missing_estimates=tasks_missing_estimates,
        developers_with_capacity=developers_with_capacity,
        developers_missing_capacity=developers_missing_capacity,
        schedule_concerns=schedule_concerns,
        missing_data_items=missing_data_items,
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
    (
        pending_tasks,
        total_effort,
        tasks_with_estimates,
        tasks_missing_estimates,
        has_unknown_effort,
        developer_capacities,
        developers_with_capacity,
        developers_missing_capacity,
        has_unknown_capacity
    ) = _get_project_tasks_and_capacities(project_id)
    
    if payload.developer_id not in developer_capacities:
        raise HTTPException(
            status_code=400,
            detail="Selected developer is not assigned to any pending tasks in this project."
        )
        
    dev_info = developer_capacities[payload.developer_id]
    dev_capacity_per_week = dev_info["capacity"]
    if dev_capacity_per_week is None:
        has_unknown_capacity = True
        dev_capacity_per_week = 0.0
    
    today = date.today()
    baseline_days = count_working_days(today, project.end_date) if project.end_date else 0
    baseline_weeks = calculate_working_weeks(today, project.end_date) if project.end_date else 0.0

    baseline_capacity = 0.0
    if project.end_date and project.end_date > today:
        for dev in developer_capacities.values():
            if dev["capacity"] is not None:
                baseline_capacity += dev["capacity"] * baseline_weeks
            
    # Calculate unavailability period
    absence_start = max(payload.start_date, today)
    absence_end = payload.end_date
    if project.end_date and absence_end > project.end_date:
        absence_end = project.end_date
        
    capacity_removed = 0.0
    absence_days = count_working_days(absence_start, absence_end)
    if absence_end > absence_start and dev_capacity_per_week > 0:
        absence_weeks = calculate_working_weeks(absence_start, absence_end)
        capacity_removed = absence_weeks * dev_capacity_per_week
        
    scenario_capacity = max(0.0, baseline_capacity - capacity_removed)
    affected_tasks = [t for t in pending_tasks if str(t.get("assigned_to")) == payload.developer_id]
    
    schedule_concerns: List[str] = []
    missing_data_items: List[str] = []
    assumptions_and_limitations = [
        "Developer capacity assumes a uniform distribution over business working days.",
        "Simulation does not account for complex task dependencies."
    ]

    if tasks_missing_estimates > 0:
        missing_data_items.append(f"{tasks_missing_estimates} task(s) lack effort estimates.")
        assumptions_and_limitations.append(f"{tasks_missing_estimates} remaining task(s) lack estimated_hours; capacity gap is a partial estimate.")

    if developers_missing_capacity > 0:
        missing_data_items.append(f"{developers_missing_capacity} developer(s) lack weekly capacity data.")
        assumptions_and_limitations.append(f"{developers_missing_capacity} developer(s) lack weekly capacity data.")
        
    if capacity_removed > 0:
        schedule_concerns.append(f"Developer absence removes {math.ceil(capacity_removed)} hours of capacity over {absence_days} working day(s).")
        
    capacity_gap = None
    capacity_gap_status = "calculated"

    if tasks_missing_estimates > 0:
        capacity_gap = None
        capacity_gap_status = "unavailable"
        schedule_concerns.append(
            f"Some remaining tasks lack effort estimates ({tasks_missing_estimates} of {len(pending_tasks)} unestimated). Add estimates to evaluate deadline feasibility accurately."
        )
    elif len(developer_capacities) > 0 and developers_with_capacity == 0:
        capacity_gap = None
        capacity_gap_status = "unavailable"
        schedule_concerns.append("Capacity gap cannot be calculated: assigned developers lack capacity data.")
    else:
        # Capacity Gap = Available Capacity - Estimated Effort Required
        capacity_gap = scenario_capacity - total_effort
        capacity_gap_status = "calculated"
        if capacity_gap > 0:
            schedule_concerns.append(
                f"Available capacity ({scenario_capacity:.1f} hrs) exceeds estimated effort ({total_effort:.1f} hrs) by {capacity_gap:.1f} hrs, subject to scheduling constraints."
            )
        elif capacity_gap == 0:
            schedule_concerns.append("Available capacity exactly matches the estimated effort under stated assumptions.")
        else:
            shortfall = abs(capacity_gap)
            schedule_concerns.append(
                f"Projected capacity shortfall of {shortfall:.1f} hours detected: estimated effort ({total_effort:.1f} hrs) exceeds available capacity ({scenario_capacity:.1f} hrs)."
            )

    if not schedule_concerns:
        schedule_concerns.append("No immediate schedule concerns detected with this proposal.")

    baseline = SimulationBaseline(
        current_deadline=project.end_date,
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=round(total_effort, 1) if tasks_with_estimates > 0 else None,
        available_capacity_hours=math.floor(baseline_capacity),
        working_days=baseline_days,
        working_weeks=round(baseline_weeks, 2),
        tasks_with_estimates=tasks_with_estimates,
        tasks_missing_estimates=tasks_missing_estimates,
        developers_with_capacity=developers_with_capacity,
        developers_missing_capacity=developers_missing_capacity,
    )
    
    from app.services.matching_service import get_recommended_team_for_task
    
    replacements = []
    for t in affected_tasks:
        try:
            candidates = get_recommended_team_for_task(t["id"])
            candidates = [c for c in candidates if str(c["developer"]["id"]) != payload.developer_id]
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
        except Exception:
            pass

    scenario = UnavailabilitySimulationScenario(
        developer_id=payload.developer_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        capacity_removed_hours=math.ceil(capacity_removed),
        remaining_tasks=len(pending_tasks),
        remaining_estimated_effort_hours=round(total_effort, 1) if tasks_with_estimates > 0 else None,
        available_capacity_hours=math.floor(scenario_capacity),
        capacity_gap_hours=math.ceil(capacity_gap) if capacity_gap is not None else None,
        capacity_gap_status=capacity_gap_status,
        working_days=baseline_days,
        working_weeks=round(baseline_weeks, 2),
        tasks_with_estimates=tasks_with_estimates,
        tasks_missing_estimates=tasks_missing_estimates,
        developers_with_capacity=developers_with_capacity,
        developers_missing_capacity=developers_missing_capacity,
        affected_tasks=affected_tasks,
        replacements=replacements,
        schedule_concerns=schedule_concerns,
        missing_data_items=missing_data_items,
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

def get_project_simulator_data_service(project_id: str, current_user: UserOut) -> ProjectSimulatorData:
    """
    Fetch comprehensive simulator dataset for a project:
    - Incomplete tasks with their estimated_hours
    - Assigned developers with their recorded weekly capacities
    - Summary of missing estimates and capacities
    """
    project = get_project_by_id_service(project_id, current_user)
    (
        pending_tasks,
        total_effort,
        tasks_with_estimates,
        tasks_missing_estimates,
        has_unknown_effort,
        developer_capacities,
        developers_with_capacity,
        developers_missing_capacity,
        has_unknown_capacity
    ) = _get_project_tasks_and_capacities(project_id)

    # Clean task rows for frontend
    clean_tasks = []
    for t in pending_tasks:
        effort = t.get("effective_estimated_hours")
        assignee_id = str(t.get("assigned_to")) if t.get("assigned_to") else None
        dev_info = developer_capacities.get(assignee_id, {}) if assignee_id else {}
        clean_tasks.append({
            "id": t["id"],
            "title": t.get("title", ""),
            "status": t.get("status", "TODO"),
            "priority": t.get("priority", "MEDIUM"),
            "due_date": t.get("due_date"),
            "assigned_to": assignee_id,
            "assigned_to_name": dev_info.get("name") if dev_info else None,
            "estimated_hours": effort,
            "estimated_effort": effort,
            "has_estimate": effort is not None,
        })

    developer_list = [
        DeveloperCapacityInfo(
            developer_id=dev["id"],
            name=dev["name"],
            email=dev["email"],
            capacity_hours_per_week=dev["capacity"],
            has_capacity=dev["has_capacity"],
            assigned_tasks_count=dev["assigned_tasks_count"],
        )
        for dev in developer_capacities.values()
    ]

    sufficient_data = (
        len(pending_tasks) > 0
        and tasks_missing_estimates == 0
        and developers_missing_capacity == 0
    )

    return ProjectSimulatorData(
        project_id=project.id,
        project_name=project.name,
        current_deadline=project.end_date,
        total_remaining_tasks=len(pending_tasks),
        tasks_with_estimates=tasks_with_estimates,
        tasks_missing_estimates=tasks_missing_estimates,
        total_estimated_effort_hours=round(total_effort, 1),
        developers_with_capacity=developers_with_capacity,
        developers_missing_capacity=developers_missing_capacity,
        sufficient_data_for_simulation=sufficient_data,
        tasks=clean_tasks,
        developers=developer_list,
    )

def update_task_estimate_service(
    project_id: str,
    task_id: str,
    estimated_hours: float,
    current_user: UserOut,
) -> dict:
    """
    Allow PM or Admin to enter or update estimated hours for a task.
    """
    project = get_project_by_id_service(project_id, current_user)
    if current_user.role == RoleEnum.PROJECT_MANAGER and str(project.project_manager_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You do not have permission to modify tasks for this project.")

    # Validate task belongs to project
    task_resp = _db().table("tasks").select("id, title, project_id").eq("id", task_id).eq("project_id", project_id).limit(1).execute()
    if not task_resp.data:
        raise HTTPException(status_code=404, detail="Task not found in this project.")

    if hasattr(estimated_hours, "estimated_hours"):
        est_val = float(estimated_hours.estimated_hours)
    else:
        est_val = float(estimated_hours)

    update_fields = {
        "estimated_hours": est_val,
        "estimated_effort": est_val
    }

    try:
        try:
            resp = _db().table("tasks").update(update_fields).eq("id", task_id).execute()
        except Exception as err:
            err_str = str(err).lower()
            if "estimated_hours" in err_str:
                update_fields.pop("estimated_hours", None)
                resp = _db().table("tasks").update(update_fields).eq("id", task_id).execute()
            else:
                raise err

        if not resp.data:
            raise HTTPException(status_code=500, detail="Database error updating task estimate.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update task estimate: {str(exc)}")

    log_activity(
        user_id=current_user.id,
        action="TASK_UPDATED",
        entity_type="task",
        entity_id=task_id,
        project_id=project_id,
        description=f"Updated estimated effort to {est_val} hours for task '{task_resp.data[0].get('title')}'"
    )

    return {
        "task_id": task_id,
        "estimated_hours": est_val,
        "estimated_effort": est_val,
        "message": f"Successfully updated estimate to {est_val} hours."
    }

def update_developer_capacity_service(
    project_id: str,
    developer_id: str,
    capacity_hours_per_week: float,
    current_user: UserOut,
) -> DeveloperCapacityInfo:
    """
    Allow PM or Admin to enter or update weekly available hours for an assigned developer.
    Persists into profiles (and users if column exists).
    """
    project = get_project_by_id_service(project_id, current_user)
    if current_user.role == RoleEnum.PROJECT_MANAGER and str(project.project_manager_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You do not have permission to manage developers for this project.")

    # Check user exists
    user_resp = _db().table("users").select("id, name, email, role").eq("id", developer_id).limit(1).execute()
    if not user_resp.data:
        raise HTTPException(status_code=404, detail=f"Developer with ID '{developer_id}' not found.")
    dev_user = user_resp.data[0]

    if hasattr(capacity_hours_per_week, "capacity_hours_per_week"):
        cap_val = float(capacity_hours_per_week.capacity_hours_per_week)
    else:
        cap_val = float(capacity_hours_per_week)

    cap_int = int(round(cap_val))

    # 1. Update / Upsert in profiles table
    try:
        prof_resp = _db().table("profiles").select("id").eq("id", developer_id).limit(1).execute()
        if prof_resp.data:
            _db().table("profiles").update({"capacity_hours_per_week": cap_int}).eq("id", developer_id).execute()
        else:
            _db().table("profiles").insert({
                "id": developer_id,
                "full_name": dev_user.get("name", "Unknown"),
                "email": dev_user.get("email"),
                "role": dev_user.get("role", "developer").upper(),
                "capacity_hours_per_week": cap_int
            }).execute()
    except Exception as exc:
        print(f"Notice: failed writing capacity to profiles: {exc}")

    # 2. Try updating users table as well if column exists
    try:
        _db().table("users").update({"capacity_hours_per_week": cap_int}).eq("id", developer_id).execute()
    except Exception:
        pass

    log_activity(
        user_id=current_user.id,
        action="DEVELOPER_CAPACITY_UPDATED",
        entity_type="user",
        entity_id=developer_id,
        project_id=project_id,
        description=f"Set weekly capacity to {cap_val} hours for developer '{dev_user.get('name')}'"
    )

    return DeveloperCapacityInfo(
        developer_id=developer_id,
        name=dev_user.get("name", "Unknown"),
        email=dev_user.get("email"),
        capacity_hours_per_week=cap_val,
        has_capacity=True,
    )
