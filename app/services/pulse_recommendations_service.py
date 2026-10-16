"""
Pulse Recommendations Service — Phase 4 AI Project Pulse.

Generates practical, evidence-grounded recovery recommendations for project managers.
Reuses:
- Continuity context-building (build_project_context from app.services.continuity_service)
- Smart Allocation (get_recommended_team_for_task from app.services.matching_service)
- Existing workflow risks table and deterministic health score formulas.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from fastapi import HTTPException
from app.db.supabase_client import get_supabase_client
from app.schemas.pulse import (
    PulseRecommendation,
    SupportingReference,
    ProjectPulseRecommendationsResponse,
)
from app.services.continuity_service import build_project_context
from app.services.matching_service import get_recommended_team_for_task
from app.services.ai_service import ask_llm

logger = logging.getLogger(__name__)


def _db():
    return get_supabase_client()


def is_task_overdue(task: dict, now: Optional[datetime] = None) -> bool:
    """Deterministic check if a task is overdue."""
    if now is None:
        now = datetime.now(timezone.utc)
    due_date_raw = task.get("due_date")
    status = (task.get("status") or "").upper()
    if not due_date_raw or status == "COMPLETED":
        return False
    try:
        if "T" in str(due_date_raw):
            due_dt = datetime.fromisoformat(str(due_date_raw).replace("Z", "+00:00"))
        else:
            due_dt = datetime.fromisoformat(f"{due_date_raw}T23:59:59+00:00")
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        return due_dt < now
    except Exception:
        return False


def calculate_deterministic_health(
    project: dict, tasks: List[dict], risks: List[dict], now: Optional[datetime] = None
) -> Tuple[int, str, List[Dict[str, Any]]]:
    """
    Approved deterministic health score formula from Phase 1-3.
    Returns: (score, data_completeness, components)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    score = 100
    components = []
    p_total = len(tasks)
    has_dates = bool(project.get("start_date") and project.get("end_date"))
    has_tasks = p_total > 0

    completeness_factors = []
    if not has_tasks:
        completeness_factors.append("No tasks")
    if not has_dates:
        completeness_factors.append("Missing schedule dates")

    if not has_tasks and not has_dates:
        data_completeness = "Low"
    elif not has_tasks or not has_dates:
        data_completeness = "Medium"
    else:
        data_completeness = "High"

    # Only count unresolved risks
    unresolved_risks = [
        r for r in risks
        if not r.get("is_resolved") and (r.get("status") or "").upper() != "RESOLVED"
    ]
    task_ids_with_risks = {
        str(r.get("task_id")).lower() for r in unresolved_risks if r.get("task_id")
    }

    crit_count = sum(1 for r in unresolved_risks if str(r.get("level", "")).lower() == "critical")
    high_count = sum(1 for r in unresolved_risks if str(r.get("level", "")).lower() == "high")
    med_count = sum(1 for r in unresolved_risks if str(r.get("level", "")).lower() == "medium")

    if crit_count > 0:
        amt = crit_count * 15
        score -= amt
        components.append({"reason": f"{crit_count} Critical Risk(s)", "amount": amt})
    if high_count > 0:
        amt = high_count * 10
        score -= amt
        components.append({"reason": f"{high_count} High Risk(s)", "amount": amt})
    if med_count > 0:
        amt = med_count * 5
        score -= amt
        components.append({"reason": f"{med_count} Medium Risk(s)", "amount": amt})

    # Overdue tasks (avoid double counting if tracked by risk)
    overdue_no_risk = [
        t for t in tasks
        if is_task_overdue(t, now) and str(t.get("id", "")).lower() not in task_ids_with_risks
    ]
    if overdue_no_risk:
        amt = len(overdue_no_risk) * 3
        score -= amt
        components.append({"reason": f"{len(overdue_no_risk)} Overdue Task(s) (Unmitigated)", "amount": amt})

    if has_dates:
        try:
            st_raw = str(project["start_date"]).replace("Z", "+00:00")
            en_raw = str(project["end_date"]).replace("Z", "+00:00")
            st_dt = datetime.fromisoformat(st_raw)
            en_dt = datetime.fromisoformat(en_raw)
            if st_dt.tzinfo is None:
                st_dt = st_dt.replace(tzinfo=timezone.utc)
            if en_dt.tzinfo is None:
                en_dt = en_dt.replace(tzinfo=timezone.utc)

            if now > st_dt and en_dt > st_dt:
                elapsed = min((now - st_dt).total_seconds() / (en_dt - st_dt).total_seconds(), 1.0)
                exp_prog = int(elapsed * 100)
                act_prog = project.get("progress") or 0
                if act_prog < exp_prog - 10:
                    amt = exp_prog - act_prog
                    score -= amt
                    components.append({
                        "reason": f"Schedule lag (Expected {exp_prog}%, Actual {act_prog}%)",
                        "amount": amt
                    })
        except Exception:
            pass

    if data_completeness != "High":
        components.append({
            "reason": f"Data completeness is {data_completeness}: {', '.join(completeness_factors)}",
            "amount": 0
        })

    return max(0, score), data_completeness, components


async def build_compact_evidence_payload(project_id: str) -> dict:
    """
    Requirement 1 & 2:
    Builds a compact project evidence payload by reusing existing AI Continuity context
    and Smart Allocation services.
    """
    # Reuse existing AI Continuity context builder
    _, raw_dict = await build_project_context(project_id)
    project = raw_dict["project"]
    tasks = raw_dict["tasks"]
    activities = raw_dict["activities"]
    risks = raw_dict["risks"]
    github = raw_dict.get("github") or {}

    now = datetime.now(timezone.utc)
    health_score, data_completeness, health_components = calculate_deterministic_health(
        project, tasks, risks, now
    )

    # Developers and workloads
    # Fetch profiles to obtain capacity and developer details
    try:
        profiles_resp = _db().table("profiles").select("*").execute()
        profiles_list = profiles_resp.data or []
        profiles_map = {str(p["id"]).lower(): p for p in profiles_list}
    except Exception:
        profiles_map = {}

    try:
        users_resp = _db().table("users").select("id, name, email, role").execute()
        users_list = users_resp.data or []
        users_map = {str(u["id"]).lower(): u for u in users_list}
    except Exception:
        users_map = {}

    # Compile developer workloads across the project
    dev_task_counts: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        assignee_id = str(t.get("assigned_to") or "").lower()
        if not assignee_id:
            continue
        if assignee_id not in dev_task_counts:
            profile = profiles_map.get(assignee_id, {})
            user_info = users_map.get(assignee_id, {})
            name = profile.get("full_name") or user_info.get("name") or "Developer"
            capacity = profile.get("capacity_hours_per_week") or 40
            dev_task_counts[assignee_id] = {
                "developer_id": assignee_id,
                "name": name,
                "capacity_hours": capacity,
                "active_tasks": 0,
                "overdue_tasks": 0,
                "total_tasks": 0,
            }
        dev_task_counts[assignee_id]["total_tasks"] += 1
        t_status = (t.get("status") or "").upper()
        if t_status in ("IN_PROGRESS", "TODO", "REVIEW"):
            dev_task_counts[assignee_id]["active_tasks"] += 1
        if is_task_overdue(t, now):
            dev_task_counts[assignee_id]["overdue_tasks"] += 1

    workload_summary = []
    for d_id, data in dev_task_counts.items():
        # Heuristic: 1 task ~ 10 hours
        utilization = (data["active_tasks"] * 10) / max(data["capacity_hours"], 1)
        workload_summary.append({
            "developer_id": d_id,
            "developer_name": data["name"],
            "active_tasks": data["active_tasks"],
            "overdue_tasks": data["overdue_tasks"],
            "capacity_hours": data["capacity_hours"],
            "utilization_pct": round(utilization * 100),
            "is_overloaded": data["active_tasks"] >= 4 or utilization > 1.0,
        })

    # Filter critical & high unresolved risks
    unresolved_risks = [
        r for r in risks
        if not r.get("is_resolved") and (r.get("status") or "").upper() != "RESOLVED"
    ]
    critical_or_high_risks = [
        {
            "id": str(r.get("id")),
            "title": r.get("title", ""),
            "severity": r.get("level", "Medium"),
            "risk_type": r.get("risk_type", "UNKNOWN"),
            "task_id": r.get("task_id"),
            "detected_at": str(r.get("detected_at") or r.get("created_at") or ""),
            "description": r.get("description", ""),
        }
        for r in unresolved_risks
        if str(r.get("level", "")).lower() in ("critical", "high")
    ]

    # Filter overdue and blocked tasks
    overdue_tasks = []
    for t in tasks:
        if is_task_overdue(t, now):
            assignee_id = str(t.get("assigned_to") or "").lower()
            dev_name = users_map.get(assignee_id, {}).get("name") if assignee_id else "Unassigned"
            overdue_tasks.append({
                "id": str(t.get("id")),
                "title": t.get("title", ""),
                "status": (t.get("status") or "").upper(),
                "priority": (t.get("priority") or "MEDIUM").upper(),
                "due_date": str(t.get("due_date", "")),
                "assigned_to": assignee_id or None,
                "assignee_name": dev_name,
            })

    # Upcoming deadlines within next 5 days
    upcoming_deadlines = []
    for t in tasks:
        t_status = (t.get("status") or "").upper()
        if t_status == "COMPLETED" or is_task_overdue(t, now):
            continue
        due_date_raw = t.get("due_date")
        if not due_date_raw:
            continue
        try:
            if "T" in str(due_date_raw):
                due_dt = datetime.fromisoformat(str(due_date_raw).replace("Z", "+00:00"))
            else:
                due_dt = datetime.fromisoformat(f"{due_date_raw}T23:59:59+00:00")
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            days_left = (due_dt - now).total_seconds() / 86400.0
            if 0 <= days_left <= 5:
                assignee_id = str(t.get("assigned_to") or "").lower()
                dev_name = users_map.get(assignee_id, {}).get("name") if assignee_id else "Unassigned"
                upcoming_deadlines.append({
                    "id": str(t.get("id")),
                    "title": t.get("title", ""),
                    "due_date": str(due_date_raw),
                    "days_remaining": round(days_left, 1),
                    "status": t_status,
                    "assignee_name": dev_name,
                })
        except Exception:
            pass

    # Reuse Smart Allocation for overdue or unassigned tasks
    smart_allocation_suggestions = []
    tasks_to_evaluate = overdue_tasks[:2] + [
        t for t in tasks if not t.get("assigned_to") and (t.get("status") or "").upper() != "COMPLETED"
    ][:2]
    seen_task_eval = set()
    for t_item in tasks_to_evaluate:
        t_id = t_item["id"]
        if t_id in seen_task_eval:
            continue
        seen_task_eval.add(t_id)
        try:
            candidates = get_recommended_team_for_task(t_id)
            if candidates:
                top_cand = candidates[0]
                dev_obj = top_cand.get("developer", {})
                smart_allocation_suggestions.append({
                    "task_id": t_id,
                    "task_title": t_item.get("title", ""),
                    "candidate_id": str(dev_obj.get("id")),
                    "candidate_name": dev_obj.get("full_name") or dev_obj.get("name") or "Recommended Developer",
                    "match_score": top_cand.get("match_score", 0),
                    "missing_skills": top_cand.get("missing_skills", []),
                    "workload_score": top_cand.get("workload_score", 0.0),
                })
        except Exception as exc:
            logger.debug("Smart Allocation evaluation skipped for task %s: %s", t_id, exc)

    evidence = {
        "project_id": str(project.get("id")),
        "project_name": project.get("name", ""),
        "status": project.get("status", ""),
        "progress": project.get("progress", 0),
        "start_date": project.get("start_date"),
        "end_date": project.get("end_date"),
        "health_score": health_score,
        "health_data_completeness": data_completeness,
        "health_deductions": health_components,
        "critical_or_high_risks": critical_or_high_risks,
        "overdue_tasks": overdue_tasks,
        "upcoming_deadlines": upcoming_deadlines,
        "workload_metrics": workload_summary,
        "smart_allocation_suggestions": smart_allocation_suggestions,
        "recent_activities": [
            {
                "id": str(a.get("id")),
                "action": a.get("action", ""),
                "description": a.get("description", ""),
                "user_name": a.get("users", {}).get("name") if isinstance(a.get("users"), dict) else "System",
                "created_at": str(a.get("created_at") or ""),
            }
            for a in activities[:5]
        ],
        "github_summary": {
            "repository": github.get("repository"),
            "recent_commits_count": len(github.get("commits", [])),
            "open_prs_count": len(github.get("pull_requests", [])),
            "branches_count": len(github.get("branches", [])),
        },
        "raw_tasks_count": len(tasks),
        "raw_risks_count": len(risks),
    }

    return evidence


def generate_safe_deterministic_recommendations(evidence: dict) -> List[PulseRecommendation]:
    """
    Requirement 8:
    Safe, deterministic recommendations when Gemini is unavailable, rate-limited,
    times out, or returns invalid output.
    All recommendations are strictly derived from evidence data.
    """
    recs: List[PulseRecommendation] = []
    rec_counter = 1

    # 1. Resolve Critical or High Risks
    for risk in evidence.get("critical_or_high_risks", []):
        r_id = risk["id"]
        r_title = risk["title"]
        severity = risk["severity"]
        r_type = risk["risk_type"]
        penalty = 15 if severity.lower() == "critical" else 10

        recs.append(PulseRecommendation(
            id=f"det-rec-{rec_counter}",
            category="RISK_MITIGATION",
            title=f"Resolve {severity} Risk: {r_title}",
            explanation=f"A {severity} workflow risk ({r_type}) is unresolved. {risk.get('description', '')}".strip(),
            why_it_matters=f"Unresolved {severity} risks incur a deterministic -{penalty} health point deduction and threaten project delivery.",
            missing_info_or_assumptions="Technical mitigation plan and estimated resolution timestamp require confirmation from team lead.",
            confidence="High",
            confidence_rationale="Derived directly from verified open workflow risk and deterministic health score penalty formula.",
            supporting_references=[
                SupportingReference(type="risk", id=r_id, label=f"{severity} Risk: {r_title}")
            ],
            actionable_entity_id=r_id,
            actionable_entity_type="risk",
        ))
        rec_counter += 1

    # 2. Review Overdue Tasks
    for ot in evidence.get("overdue_tasks", []):
        t_id = ot["id"]
        t_title = ot["title"]
        due_date = ot["due_date"]
        status = ot["status"]
        assignee = ot["assignee_name"]

        recs.append(PulseRecommendation(
            id=f"det-rec-{rec_counter}",
            category="TASK_REVIEW",
            title=f"Review Overdue Task: {t_title}",
            explanation=f"Task was scheduled to be completed by {due_date} but remains in {status} state (assigned to {assignee}).",
            why_it_matters="Overdue tasks delay milestone progress and apply an unmitigated penalty to project health.",
            missing_info_or_assumptions="Assumes current status in DevFlow reflects actual progress; verify if code is under review or blocked.",
            confidence="High",
            confidence_rationale="Defensible deterministic comparison between task due date and current UTC timestamp.",
            supporting_references=[
                SupportingReference(type="task", id=t_id, label=f"Task: {t_title} (Due {due_date})")
            ],
            actionable_entity_id=t_id,
            actionable_entity_type="task",
        ))
        rec_counter += 1

    # 3. Consider Developer Recommendation from Smart Allocation
    for alloc in evidence.get("smart_allocation_suggestions", []):
        t_id = alloc["task_id"]
        t_title = alloc["task_title"]
        c_id = alloc["candidate_id"]
        c_name = alloc["candidate_name"]
        m_score = alloc["match_score"]

        recs.append(PulseRecommendation(
            id=f"det-rec-{rec_counter}",
            category="ALLOCATION_OPPORTUNITY",
            title=f"Consider Assigning {c_name} to Task: {t_title}",
            explanation=f"Smart Allocation ranked {c_name} as top candidate with a {m_score}% match score based on skill alignment and current workload.",
            why_it_matters="Reallocating or assigning this task leverages available developer capacity to expedite task recovery.",
            missing_info_or_assumptions="Assumes developer's scheduled capacity matches sprint availability. Requires PM consultation.",
            confidence="High",
            confidence_rationale="Computed using established Smart Allocation weighted scoring (Skills 50%, Experience 20%, Workload 20%, History 10%).",
            supporting_references=[
                SupportingReference(type="task", id=t_id, label=f"Task: {t_title}"),
                SupportingReference(type="developer", id=c_id, label=f"Developer: {c_name} ({m_score}% match)"),
            ],
            actionable_entity_id=t_id,
            actionable_entity_type="task",
        ))
        rec_counter += 1

    # 4. Investigate Workload Imbalance
    overloaded_devs = [w for w in evidence.get("workload_metrics", []) if w.get("is_overloaded")]
    available_devs = [w for w in evidence.get("workload_metrics", []) if w.get("active_tasks", 0) <= 1]
    if overloaded_devs and available_devs:
        o_dev = overloaded_devs[0]
        a_dev = available_devs[0]
        recs.append(PulseRecommendation(
            id=f"det-rec-{rec_counter}",
            category="WORKLOAD_REBALANCE",
            title=f"Investigate Workload Imbalance: {o_dev['developer_name']}",
            explanation=f"{o_dev['developer_name']} has {o_dev['active_tasks']} active tasks ({o_dev['utilization_pct']}% utilization), while {a_dev['developer_name']} has {a_dev['active_tasks']} active tasks.",
            why_it_matters="Overloaded developers create code review and delivery bottlenecks, elevating project risk.",
            missing_info_or_assumptions="Assumes tasks are of roughly comparable complexity; verify task estimations with team.",
            confidence="Medium",
            confidence_rationale="Derived from active task count distribution across developer profiles.",
            supporting_references=[
                SupportingReference(type="workload", id=o_dev["developer_id"], label=f"{o_dev['developer_name']} ({o_dev['active_tasks']} active tasks)"),
                SupportingReference(type="workload", id=a_dev["developer_id"], label=f"{a_dev['developer_name']} ({a_dev['active_tasks']} active tasks)"),
            ],
            actionable_entity_id=o_dev["developer_id"],
            actionable_entity_type="developer",
        ))
        rec_counter += 1

    # 5. Confirm Upcoming Deadlines
    for ud in evidence.get("upcoming_deadlines", [])[:2]:
        t_id = ud["id"]
        t_title = ud["title"]
        due_date = ud["due_date"]
        days = ud["days_remaining"]
        recs.append(PulseRecommendation(
            id=f"det-rec-{rec_counter}",
            category="DEADLINE_CONFIRMATION",
            title=f"Confirm Upcoming Deadline: {t_title}",
            explanation=f"Task is due in {days} days ({due_date}) and is currently in {ud['status']} state assigned to {ud['assignee_name']}.",
            why_it_matters="Proactive confirmation prevents unexpected slips and keeps project health score stable.",
            missing_info_or_assumptions="Assumes milestone dependency chain is intact. Verify pull request status.",
            confidence="Medium",
            confidence_rationale="Identified via schedule proximity window (within 5 days of target completion).",
            supporting_references=[
                SupportingReference(type="task", id=t_id, label=f"Task: {t_title} (Due {due_date})")
            ],
            actionable_entity_id=t_id,
            actionable_entity_type="task",
        ))
        rec_counter += 1

    # 6. Default if no risks, no overdue tasks, and no imbalances
    if not recs:
        recs.append(PulseRecommendation(
            id=f"det-rec-{rec_counter}",
            category="GENERAL",
            title="Maintain Current Delivery Cadence",
            explanation="No critical risks, overdue tasks, or severe workload imbalances were detected for this project.",
            why_it_matters="Project health is in a stable state. Regular sprint monitoring preserves team velocity.",
            missing_info_or_assumptions="Assumes ongoing developer tasks and time tracking are updated accurately.",
            confidence="High",
            confidence_rationale="Full alignment with deterministic health score (no active deductions found).",
            supporting_references=[],
            actionable_entity_id=evidence.get("project_id"),
            actionable_entity_type="project",
        ))

    return recs


def validate_and_sanitize_recommendations(
    raw_recs: List[dict], evidence: dict
) -> List[PulseRecommendation]:
    """
    Requirement 7:
    Validate model output against defined schema. Reject unsupported task IDs,
    developers, risk statuses, and factual claims.
    """
    valid_task_ids = {str(t["id"]).lower(): t for t in evidence.get("overdue_tasks", [])}
    # Also include all tasks known in evidence
    all_tasks = evidence.get("upcoming_deadlines", []) + evidence.get("overdue_tasks", [])
    for t in all_tasks:
        valid_task_ids[str(t["id"]).lower()] = t

    valid_risk_ids = {
        str(r["id"]).lower(): r for r in evidence.get("critical_or_high_risks", [])
    }
    valid_dev_ids = {
        str(w["developer_id"]).lower(): w for w in evidence.get("workload_metrics", [])
    }
    for sa in evidence.get("smart_allocation_suggestions", []):
        valid_dev_ids[str(sa["candidate_id"]).lower()] = sa

    valid_categories = {
        "RISK_MITIGATION",
        "TASK_REVIEW",
        "WORKLOAD_REBALANCE",
        "ALLOCATION_OPPORTUNITY",
        "DEADLINE_CONFIRMATION",
        "GENERAL",
    }
    valid_confidences = {"High", "Medium", "Low"}

    validated: List[PulseRecommendation] = []
    uuid_pattern = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
    )

    for i, item in enumerate(raw_recs):
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        explanation = str(item.get("explanation", "")).strip()
        why_it_matters = str(item.get("why_it_matters", "")).strip()
        missing_info = str(item.get("missing_info_or_assumptions", "")).strip()
        category = str(item.get("category", "GENERAL")).upper().strip()
        confidence = str(item.get("confidence", "Medium")).capitalize().strip()
        confidence_rationale = str(item.get("confidence_rationale", "")).strip()

        if not title or not explanation or not why_it_matters:
            logger.warning("Rejected recommendation #%d: missing essential text fields", i)
            continue

        if category not in valid_categories:
            category = "GENERAL"

        if confidence not in valid_confidences:
            confidence = "Medium"

        if not confidence_rationale:
            confidence_rationale = "Evaluated against project evidence and team capacity metrics."

        # Check for hallucinated UUIDs in title or explanation
        mentioned_uuids = uuid_pattern.findall(f"{title} {explanation}")
        hallucinated_uuid = False
        for uid in mentioned_uuids:
            uid_lower = uid.lower()
            if (
                uid_lower not in valid_task_ids
                and uid_lower not in valid_risk_ids
                and uid_lower not in valid_dev_ids
                and uid_lower != str(evidence.get("project_id", "")).lower()
            ):
                logger.warning("Rejected recommendation #%d: unsupported entity UUID '%s'", i, uid)
                hallucinated_uuid = True
                break

        if hallucinated_uuid:
            continue

        # Validate supporting references
        raw_refs = item.get("supporting_references", [])
        clean_refs: List[SupportingReference] = []
        rejected_ref = False

        if isinstance(raw_refs, list):
            for ref in raw_refs:
                if not isinstance(ref, dict):
                    continue
                r_type = str(ref.get("type", "")).lower().strip()
                r_id = str(ref.get("id", "")).strip()
                r_label = str(ref.get("label", "")).strip() or r_id

                if not r_id:
                    continue

                r_id_lower = r_id.lower()
                if r_type == "task":
                    if r_id_lower not in valid_task_ids:
                        logger.warning("Rejected reference: unsupported task ID '%s'", r_id)
                        rejected_ref = True
                        break
                elif r_type == "risk":
                    if r_id_lower not in valid_risk_ids:
                        logger.warning("Rejected reference: unsupported risk ID '%s'", r_id)
                        rejected_ref = True
                        break
                elif r_type in ("developer", "workload"):
                    if r_id_lower not in valid_dev_ids:
                        logger.warning("Rejected reference: unsupported developer ID '%s'", r_id)
                        rejected_ref = True
                        break

                clean_refs.append(SupportingReference(type=r_type, id=r_id, label=r_label))

        if rejected_ref:
            continue

        # Factual claim verification:
        # If recommendation categorizes as RISK_MITIGATION, ensure there is a valid risk reference
        if category == "RISK_MITIGATION":
            has_valid_risk = any(ref.type == "risk" for ref in clean_refs)
            if not has_valid_risk and not evidence.get("critical_or_high_risks"):
                logger.warning("Rejected recommendation: claims risk mitigation but no risks exist")
                continue

        # If recommendation categorizes as TASK_REVIEW, ensure there is a valid task reference
        if category == "TASK_REVIEW":
            has_valid_task = any(ref.type == "task" for ref in clean_refs)
            if not has_valid_task and not evidence.get("overdue_tasks"):
                logger.warning("Rejected recommendation: claims overdue task review but no overdue tasks exist")
                continue

        validated.append(PulseRecommendation(
            id=item.get("id") or f"ai-rec-{len(validated) + 1}",
            category=category,
            title=title,
            explanation=explanation,
            why_it_matters=why_it_matters,
            missing_info_or_assumptions=missing_info or "Requires PM review with assigned developer.",
            confidence=confidence,
            confidence_rationale=confidence_rationale,
            supporting_references=clean_refs,
            actionable_entity_id=item.get("actionable_entity_id"),
            actionable_entity_type=item.get("actionable_entity_type"),
        ))

    return validated


async def generate_pulse_recommendations_service(
    project_id: str,
) -> ProjectPulseRecommendationsResponse:
    """
    Main entry point for generating Phase 4 AI Project Pulse recovery recommendations.
    Adheres strictly to requirements:
    - Never calculates or overrides health score.
    - Grounded in compact evidence payload.
    - Validates against schema, rejects unsupported entities.
    - Falls back safely to deterministic recommendations on AI unavailability, quota limits, or errors.
    """
    evidence = await build_compact_evidence_payload(project_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Pre-render compact evidence as JSON for prompt
    evidence_json = json.dumps(evidence, indent=2)

    prompt = f"""
You are the AI Project Pulse Recovery Advisor for the 'AI DevFlow Intelligence Suite'.
Your role is to provide practical, evidence-grounded recovery recommendations for a Project Manager.

CRITICAL INSTRUCTIONS & CONSTRAINTS:
1. Use ONLY the grounded facts in the PROJECT EVIDENCE below.
2. DO NOT invent, hallucinate, or reference tasks, developers, or risks not present in the evidence.
3. DO NOT recalculate, modify, or override the health score ({evidence['health_score']}/100). The score is strictly deterministic.
4. Generate 2 to 5 practical recovery recommendations focusing on:
   - Resolving specific Critical or High risks.
   - Reviewing specific overdue or blocked tasks.
   - Investigating workload imbalances supported by actual capacity data.
   - Considering developer recommendations from Smart Allocation results.
   - Confirming upcoming deadlines when data exists.
5. Every recommendation MUST include:
   - 'title': Actionable and concise.
   - 'explanation': Clear explanation of the observation.
   - 'why_it_matters': Impact on project health score or delivery milestones.
   - 'missing_info_or_assumptions': Known unknowns or assumptions made.
   - 'confidence': One of 'High', 'Medium', or 'Low'.
   - 'confidence_rationale': Defensible, documented method (e.g. deterministic risk penalty, capacity ratio, deadline proximity).
   - 'category': One of ['RISK_MITIGATION', 'TASK_REVIEW', 'WORKLOAD_REBALANCE', 'ALLOCATION_OPPORTUNITY', 'DEADLINE_CONFIRMATION', 'GENERAL'].
   - 'supporting_references': List of objects with {{'type': 'task'|'risk'|'developer'|'workload', 'id': '<ID from evidence>', 'label': '<description>'}}.
6. All recommendations are advisory for human review. Never prescribe automatic mutations.

PROJECT EVIDENCE:
{evidence_json}

OUTPUT FORMAT:
Respond with a valid JSON array of recommendation objects matching the schema:
[
  {{
    "id": "rec-1",
    "category": "RISK_MITIGATION",
    "title": "...",
    "explanation": "...",
    "why_it_matters": "...",
    "missing_info_or_assumptions": "...",
    "confidence": "High",
    "confidence_rationale": "...",
    "supporting_references": [
      {{ "type": "risk", "id": "...", "label": "..." }}
    ],
    "actionable_entity_id": "...",
    "actionable_entity_type": "risk"
  }}
]
"""

    ai_recs: List[PulseRecommendation] = []
    source = "gemini"
    ai_status = "SUCCESS"
    ai_notice = None

    try:
        raw_text = await ask_llm(prompt, json_response=True)
        clean_text = raw_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]

        parsed = json.loads(clean_text)
        if isinstance(parsed, dict) and "recommendations" in parsed:
            parsed = parsed["recommendations"]

        if isinstance(parsed, list):
            ai_recs = validate_and_sanitize_recommendations(parsed, evidence)

        if not ai_recs:
            logger.warning(
                "Gemini output for project %s produced 0 valid recommendations after validation; falling back.",
                project_id
            )
            source = "deterministic"
            ai_status = "FALLBACK"
            ai_notice = "AI recommendations failed entity verification. Displaying safe deterministic recovery recommendations."
            ai_recs = generate_safe_deterministic_recommendations(evidence)

    except HTTPException as http_exc:
        logger.warning(
            "AI service returned HTTP %s for project %s: %s",
            http_exc.status_code, project_id, http_exc.detail
        )
        source = "deterministic"
        ai_status = "FALLBACK"
        if http_exc.status_code == 429:
            ai_notice = "AI service is currently quota-limited. Displaying safe deterministic recovery recommendations."
        else:
            ai_notice = f"AI assistance unavailable ({http_exc.detail}). Displaying safe deterministic recovery recommendations."
        ai_recs = generate_safe_deterministic_recommendations(evidence)

    except Exception as exc:
        logger.error(
            "Unexpected error querying AI for pulse recommendations on project %s: %s",
            project_id, exc
        )
        source = "deterministic"
        ai_status = "FALLBACK"
        ai_notice = f"AI model response could not be processed. Displaying safe deterministic recovery recommendations."
        ai_recs = generate_safe_deterministic_recommendations(evidence)

    return ProjectPulseRecommendationsResponse(
        project_id=str(evidence.get("project_id")),
        project_name=str(evidence.get("project_name")),
        health_score=evidence.get("health_score", 100),
        health_data_completeness=evidence.get("health_data_completeness", "High"),
        recommendations=ai_recs,
        source=source,
        ai_status=ai_status,
        ai_notice=ai_notice,
        generated_at=now_iso,
    )
