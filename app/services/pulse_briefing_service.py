"""
Pulse Briefing Service — Phase 5 AI Project Pulse.

Generates a concise, current, evidence-grounded project briefing for project managers.
Reuses:
- AI Continuity context builder (build_project_context from app.services.continuity_service)
- Deterministic health calculation (calculate_deterministic_health, is_task_overdue from app.services.pulse_recommendations_service)
- ai_service ask_llm with strict grounding and graceful deterministic fallbacks.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from fastapi import HTTPException
from app.db.supabase_client import get_supabase_client
from app.schemas.pulse_briefing import (
    HealthSection,
    CompletedWorkSection,
    InProgressAndPendingSection,
    OverdueOrBlockedSection,
    UnresolvedRisksSection,
    RecentActivitySection,
    GitHubContextSection,
    RecommendedNextActionsSection,
    DataGapsSection,
    PulseBriefingSections,
    ProjectPulseBriefingResponse,
)
from app.services.continuity_service import build_project_context
from app.services.pulse_recommendations_service import (
    calculate_deterministic_health,
    is_task_overdue,
)
from app.services.ai_service import ask_llm

logger = logging.getLogger(__name__)


def _db():
    return get_supabase_client()


def build_verified_briefing_facts(project_id: str, raw_dict: dict, now: datetime) -> dict:
    """
    Extracts and organizes strictly verified facts from the project context.
    Guarantees:
    - Never infers task completion from commit messages or branches.
    - Never treats missing GitHub data as project failure.
    - Detects explicit data gaps that affect briefing reliability.
    """
    project = raw_dict["project"]
    tasks = raw_dict["tasks"]
    activities = raw_dict["activities"]
    risks = raw_dict["risks"]
    github = raw_dict.get("github") or {}

    # Calculate deterministic health score
    score, completeness, deductions = calculate_deterministic_health(project, tasks, risks, now)

    # 1. Tasks Categorization
    completed_tasks = [t for t in tasks if (t.get("status") or "").upper() == "COMPLETED"]
    in_progress_tasks = [t for t in tasks if (t.get("status") or "").upper() in ("IN_PROGRESS", "REVIEW")]
    pending_tasks = [t for t in tasks if (t.get("status") or "").upper() in ("TODO", "PENDING")]
    overdue_tasks = [t for t in tasks if is_task_overdue(t, now)]

    # 2. Risks Categorization
    unresolved_risks = [
        r for r in risks
        if not r.get("is_resolved") and (r.get("status") or "").upper() != "RESOLVED"
    ]
    critical_risks = [r for r in unresolved_risks if str(r.get("level", "")).lower() == "critical"]
    high_risks = [r for r in unresolved_risks if str(r.get("level", "")).lower() == "high"]
    medium_risks = [r for r in unresolved_risks if str(r.get("level", "")).lower() == "medium"]

    # 3. GitHub Data
    has_github = bool(github and github.get("repository"))
    commits = github.get("commits", []) if has_github else []
    pull_requests = github.get("pull_requests", []) if has_github else []
    branches = github.get("branches", []) if has_github else []

    # 4. Identify Data Gaps
    data_gaps = []
    if not tasks:
        data_gaps.append("Project has no tasks created in DevFlow.")
    if not project.get("start_date") or not project.get("end_date"):
        data_gaps.append("Missing milestone start or end dates; schedule lag cannot be fully evaluated.")
    if not has_github:
        data_gaps.append("GitHub repository integration is not connected (progress tracked via internal tasks only).")
    unassigned_count = sum(1 for t in tasks if not t.get("assigned_to"))
    if unassigned_count > 0:
        data_gaps.append(f"{unassigned_count} task(s) currently lack an assigned developer.")
    missing_due_dates = sum(1 for t in tasks if not t.get("due_date") and (t.get("status") or "").upper() != "COMPLETED")
    if missing_due_dates > 0:
        data_gaps.append(f"{missing_due_dates} active task(s) do not have target due dates.")

    # 5. Extract Recent Activities (max 10)
    recent_acts = []
    for a in activities[:10]:
        user_name = a.get("users", {}).get("name") if isinstance(a.get("users"), dict) else "System"
        recent_acts.append({
            "action": a.get("action", "UPDATE"),
            "description": a.get("description", ""),
            "actor": user_name,
            "timestamp": str(a.get("created_at") or ""),
        })

    facts = {
        "project_id": str(project.get("id")),
        "project_name": project.get("name", "Unnamed Project"),
        "status": project.get("status", "active"),
        "progress_pct": project.get("progress", 0),
        "start_date": project.get("start_date"),
        "end_date": project.get("end_date"),
        "health_score": score,
        "health_completeness": completeness,
        "health_deductions": deductions,
        "total_tasks_count": len(tasks),
        "completed_tasks": [
            {
                "id": str(t["id"]),
                "title": t.get("title", ""),
                "priority": t.get("priority", "MEDIUM"),
            }
            for t in completed_tasks
        ],
        "in_progress_tasks": [
            {
                "id": str(t["id"]),
                "title": t.get("title", ""),
                "status": t.get("status", ""),
                "priority": t.get("priority", "MEDIUM"),
                "due_date": str(t.get("due_date", "")),
            }
            for t in in_progress_tasks
        ],
        "pending_tasks": [
            {
                "id": str(t["id"]),
                "title": t.get("title", ""),
                "status": t.get("status", ""),
                "priority": t.get("priority", "MEDIUM"),
            }
            for t in pending_tasks
        ],
        "overdue_tasks": [
            {
                "id": str(t["id"]),
                "title": t.get("title", ""),
                "due_date": str(t.get("due_date", "")),
                "status": t.get("status", ""),
            }
            for t in overdue_tasks
        ],
        "critical_risks": [
            {
                "id": str(r["id"]),
                "title": r.get("title", ""),
                "severity": "Critical",
                "risk_type": r.get("risk_type", "UNKNOWN"),
                "detected_at": str(r.get("detected_at") or r.get("created_at") or ""),
                "description": r.get("description", ""),
            }
            for r in critical_risks
        ],
        "high_risks": [
            {
                "id": str(r["id"]),
                "title": r.get("title", ""),
                "severity": "High",
                "risk_type": r.get("risk_type", "UNKNOWN"),
                "detected_at": str(r.get("detected_at") or r.get("created_at") or ""),
                "description": r.get("description", ""),
            }
            for r in high_risks
        ],
        "medium_risks_count": len(medium_risks),
        "recent_activities": recent_acts,
        "github": {
            "is_configured": has_github,
            "repository": github.get("repository") if has_github else None,
            "commits_count": len(commits),
            "open_prs_count": len(pull_requests),
            "branches_count": len(branches),
        },
        "data_gaps": data_gaps,
    }
    return facts


def generate_safe_deterministic_briefing(facts: dict) -> PulseBriefingSections:
    """
    Constructs a robust, factual briefing strictly from verified facts.
    Used when Gemini is rate-limited, times out, is unavailable, or returns invalid JSON.
    """
    score = facts["health_score"]
    status_label = (
        "Healthy" if score >= 80
        else "Caution / Minor Degradation" if score >= 60
        else "At Critical Risk"
    )
    factors = [f"{d['reason']} (-{d['amount']})" for d in facts["health_deductions"] if d.get("amount", 0) > 0]
    if not factors:
        factors.append("No active deductions; project metrics are optimal.")

    health_explanation = (
        f"Project health is calculated deterministically at {score}/100 ({status_label}). "
        f"Data completeness is {facts['health_completeness']}. "
        + (f"Active deductions include: {', '.join(factors)}." if facts["health_deductions"] else "All tracked dimensions are within normal operating thresholds.")
    )

    # 1. Health Section
    health = HealthSection(
        score=score,
        status=status_label,
        data_completeness=facts["health_completeness"],
        factors=factors,
        explanation=health_explanation,
    )

    # 2. Completed Work Section
    comp_tasks = facts["completed_tasks"]
    comp_titles = [t["title"] for t in comp_tasks]
    if comp_tasks:
        comp_summary = (
            f"{len(comp_tasks)} task(s) are officially marked as completed in DevFlow: "
            f"{', '.join(comp_titles[:5])}{' and more' if len(comp_titles) > 5 else ''}."
        )
    else:
        comp_summary = "No tasks have been marked as completed in DevFlow yet."
    completed_work = CompletedWorkSection(
        tasks_count=len(comp_tasks),
        task_titles=comp_titles,
        summary=comp_summary,
    )

    # 3. In Progress and Pending Work
    ip_tasks = facts["in_progress_tasks"]
    p_tasks = facts["pending_tasks"]
    ip_titles = [t["title"] for t in ip_tasks]
    p_titles = [t["title"] for t in p_tasks]
    ip_summary = (
        f"Currently, {len(ip_tasks)} task(s) are active (in progress or review) and "
        f"{len(p_tasks)} task(s) are queued as TODO."
    )
    in_progress_and_pending = InProgressAndPendingSection(
        in_progress_count=len(ip_tasks),
        todo_count=len(p_tasks),
        in_progress_tasks=ip_titles,
        pending_tasks=p_titles,
        summary=ip_summary,
    )

    # 4. Overdue or Blocked
    overdue_tasks = facts["overdue_tasks"]
    od_items = [f"{t['title']} (Due: {t['due_date']})" for t in overdue_tasks]
    if overdue_tasks:
        od_summary = (
            f"{len(overdue_tasks)} task(s) have passed their scheduled due dates without completion: "
            f"{', '.join(od_items[:3])}."
        )
    else:
        od_summary = "No overdue or blocked tasks were detected based on current due dates."
    overdue_or_blocked = OverdueOrBlockedSection(
        count=len(overdue_tasks),
        items=od_items,
        summary=od_summary,
    )

    # 5. Unresolved Risks
    crit_risks = facts["critical_risks"]
    high_risks = facts["high_risks"]
    risk_items = [
        f"[{r['severity']}] {r['title']} ({r['risk_type']}): {r['description']}"
        for r in crit_risks + high_risks
    ]
    if risk_items:
        r_summary = (
            f"Detected {len(crit_risks)} critical and {len(high_risks)} high workflow risk(s) "
            "requiring mitigation."
        )
    else:
        r_summary = "No critical or high severity risks are currently open."
    unresolved_risks = UnresolvedRisksSection(
        critical_count=len(crit_risks),
        high_count=len(high_risks),
        items=risk_items,
        summary=r_summary,
    )

    # 6. Recent Activity
    acts = facts["recent_activities"]
    act_items = [f"{a['actor']} ({a['action']}): {a['description']} [{a['timestamp']}]" for a in acts]
    act_summary = (
        f"{len(acts)} recent project activity record(s) logged."
        if acts else "No recent project activity recorded."
    )
    recent_activity = RecentActivitySection(
        items=act_items,
        summary=act_summary,
    )

    # 7. GitHub Context
    gh = facts["github"]
    if gh["is_configured"]:
        gh_summary = (
            f"Connected to repository '{gh['repository']}'. Recent activity includes "
            f"{gh['commits_count']} recent commit(s), {gh['open_prs_count']} open PR(s), "
            f"and {gh['branches_count']} branch(es)."
        )
    else:
        gh_summary = (
            "GitHub repository integration is not configured. Project progress is tracked "
            "strictly through DevFlow tasks."
        )
    github_context = GitHubContextSection(
        is_configured=gh["is_configured"],
        repository=gh["repository"],
        recent_commits_count=gh["commits_count"],
        open_prs_count=gh["open_prs_count"],
        branches_count=gh["branches_count"],
        summary=gh_summary,
    )

    # 8. Recommended Next Actions
    actions = []
    for r in crit_risks:
        actions.append(f"Mitigate Critical Risk: {r['title']}")
    for r in high_risks:
        actions.append(f"Address High Risk: {r['title']}")
    for od in overdue_tasks[:2]:
        actions.append(f"Follow up on Overdue Task: {od['title']}")
    if not actions:
        if ip_tasks:
            actions.append(f"Monitor active task delivery: {ip_tasks[0]['title']}")
        else:
            actions.append("Maintain current project delivery cadence.")
    recommended_next_actions = RecommendedNextActionsSection(
        actions=actions,
        summary=f"Focus on {len(actions)} high-priority action(s) to stabilize project delivery.",
    )

    # 9. Data Gaps
    gaps = facts["data_gaps"]
    rel_impact = (
        "Data completeness is reduced due to missing schedule or integration data. "
        "Briefing certainty is moderate until missing dates/assignments are populated."
        if gaps else "Project data completeness is high. Minimal data gaps observed."
    )
    data_gaps = DataGapsSection(
        gaps=gaps,
        reliability_impact=rel_impact,
    )

    return PulseBriefingSections(
        health=health,
        completed_work=completed_work,
        in_progress_and_pending=in_progress_and_pending,
        overdue_or_blocked=overdue_or_blocked,
        unresolved_risks=unresolved_risks,
        recent_activity=recent_activity,
        github_context=github_context,
        recommended_next_actions=recommended_next_actions,
        data_gaps=data_gaps,
    )


def sanitize_ai_briefing(raw_json: dict, facts: dict) -> PulseBriefingSections:
    """
    Validates and sanitizes AI-generated briefing sections against ground truth facts.
    Guarantees:
    - AI cannot override the deterministic health score.
    - AI cannot invent completed tasks.
    - AI cannot invent risks.
    - Missing GitHub data is not marked as project failure.
    """
    fallback = generate_safe_deterministic_briefing(facts)

    try:
        raw_health = raw_json.get("health", {})
        raw_comp = raw_json.get("completed_work", {})
        raw_ip = raw_json.get("in_progress_and_pending", {})
        raw_od = raw_json.get("overdue_or_blocked", {})
        raw_risks = raw_json.get("unresolved_risks", {})
        raw_act = raw_json.get("recent_activity", {})
        raw_gh = raw_json.get("github_context", {})
        raw_actions = raw_json.get("recommended_next_actions", {})
        raw_gaps = raw_json.get("data_gaps", {})

        # 1. Health: Enforce deterministic score
        health = HealthSection(
            score=facts["health_score"],  # STRICTLY DETERMINISTIC
            status=str(raw_health.get("status") or fallback.health.status),
            data_completeness=facts["health_completeness"],
            factors=fallback.health.factors,
            explanation=str(raw_health.get("explanation") or fallback.health.explanation),
        )

        # 2. Completed Work: Must match actual completed task count
        completed_work = CompletedWorkSection(
            tasks_count=len(facts["completed_tasks"]),
            task_titles=[t["title"] for t in facts["completed_tasks"]],
            summary=str(raw_comp.get("summary") or fallback.completed_work.summary),
        )

        # 3. In Progress and Pending
        in_progress_and_pending = InProgressAndPendingSection(
            in_progress_count=len(facts["in_progress_tasks"]),
            todo_count=len(facts["pending_tasks"]),
            in_progress_tasks=[t["title"] for t in facts["in_progress_tasks"]],
            pending_tasks=[t["title"] for t in facts["pending_tasks"]],
            summary=str(raw_ip.get("summary") or fallback.in_progress_and_pending.summary),
        )

        # 4. Overdue or Blocked
        overdue_or_blocked = OverdueOrBlockedSection(
            count=len(facts["overdue_tasks"]),
            items=fallback.overdue_or_blocked.items,
            summary=str(raw_od.get("summary") or fallback.overdue_or_blocked.summary),
        )

        # 5. Unresolved Risks
        unresolved_risks = UnresolvedRisksSection(
            critical_count=len(facts["critical_risks"]),
            high_count=len(facts["high_risks"]),
            items=fallback.unresolved_risks.items,
            summary=str(raw_risks.get("summary") or fallback.unresolved_risks.summary),
        )

        # 6. Recent Activity
        recent_activity = RecentActivitySection(
            items=fallback.recent_activity.items,
            summary=str(raw_act.get("summary") or fallback.recent_activity.summary),
        )

        # 7. GitHub Context
        gh_summary = str(raw_gh.get("summary") or fallback.github_context.summary)
        # Ensure no claim of project failure if GitHub is absent
        if not facts["github"]["is_configured"] and "failure" in gh_summary.lower():
            gh_summary = fallback.github_context.summary

        github_context = GitHubContextSection(
            is_configured=facts["github"]["is_configured"],
            repository=facts["github"]["repository"],
            recent_commits_count=facts["github"]["commits_count"],
            open_prs_count=facts["github"]["open_prs_count"],
            branches_count=facts["github"]["branches_count"],
            summary=gh_summary,
        )

        # 8. Recommended Next Actions
        raw_act_list = raw_actions.get("actions", [])
        clean_actions = [str(a) for a in raw_act_list if isinstance(a, str) and a.strip()] or fallback.recommended_next_actions.actions
        recommended_next_actions = RecommendedNextActionsSection(
            actions=clean_actions,
            summary=str(raw_actions.get("summary") or fallback.recommended_next_actions.summary),
        )

        # 9. Data Gaps
        data_gaps = DataGapsSection(
            gaps=facts["data_gaps"],
            reliability_impact=str(raw_gaps.get("reliability_impact") or fallback.data_gaps.reliability_impact),
        )

        return PulseBriefingSections(
            health=health,
            completed_work=completed_work,
            in_progress_and_pending=in_progress_and_pending,
            overdue_or_blocked=overdue_or_blocked,
            unresolved_risks=unresolved_risks,
            recent_activity=recent_activity,
            github_context=github_context,
            recommended_next_actions=recommended_next_actions,
            data_gaps=data_gaps,
        )

    except Exception as exc:
        logger.warning("Error sanitizing AI briefing JSON; using safe deterministic fallback: %s", exc)
        return fallback


async def generate_pulse_briefing_service(project_id: str) -> ProjectPulseBriefingResponse:
    """
    Main entry point for generating Phase 5 AI Project Pulse project briefings.
    - Reuses AI Continuity context builder.
    - Strictly grounds briefing in verified facts.
    - Preserves deterministic health score.
    - Handles rate limits, timeouts, and errors with safe deterministic fallback.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    freshness_str = f"Live snapshot as of {now.strftime('%H:%M:%S UTC')}"

    # 1. Fetch verified context
    _, raw_dict = await build_project_context(project_id)
    facts = build_verified_briefing_facts(project_id, raw_dict, now)

    # 2. Build Prompt
    facts_json = json.dumps(facts, indent=2)

    prompt = f"""
You are the AI Project Pulse Executive Briefing Advisor for 'AI DevFlow Intelligence Suite'.
Generate a concise, factual, and actionable briefing for the project manager based ONLY on the verified project facts below.

STRICT GROUNDING & INTEGRITY RULES:
1. Base all statements ONLY on the supplied facts.
2. The health score is strictly {facts['health_score']}/100. DO NOT recalculate, modify, or override it.
3. Completed work MUST be based ONLY on tasks with status 'COMPLETED'. DO NOT claim a task is completed based on a commit message or branch name.
4. If GitHub integration is not configured (is_configured: false), state it clearly. DO NOT treat missing GitHub data as project failure.
5. DO NOT fabricate tasks, developers, dates, risks, or events.
6. Clearly distinguish verified facts from advisory synthesis.

VERIFIED PROJECT FACTS:
{facts_json}

OUTPUT SCHEMA:
Provide a valid JSON object matching the 9 briefing sections:
{{
  "health": {{
    "status": "Healthy" | "Caution" | "At Risk",
    "explanation": "Concise explanation of the score and active factors."
  }},
  "completed_work": {{
    "summary": "Summary of verified completed tasks."
  }},
  "in_progress_and_pending": {{
    "summary": "Summary of active in-progress and queued work."
  }},
  "overdue_or_blocked": {{
    "summary": "Summary of overdue or blocked tasks, or none if empty."
  }},
  "unresolved_risks": {{
    "summary": "Summary of active critical and high risks."
  }},
  "recent_activity": {{
    "summary": "Summary of recent developer actions and timeline updates."
  }},
  "github_context": {{
    "summary": "Summary of repo commits/PRs, or note that GitHub is not connected."
  }},
  "recommended_next_actions": {{
    "actions": ["Prioritized action 1", "Prioritized action 2"],
    "summary": "Recommended next focus for PM."
  }},
  "data_gaps": {{
    "reliability_impact": "Explanation of how data gaps affect briefing certainty."
  }}
}}
"""

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
        sections = sanitize_ai_briefing(parsed, facts)

    except HTTPException as http_exc:
        logger.warning(
            "AI service returned HTTP %s for pulse briefing on project %s: %s",
            http_exc.status_code, project_id, http_exc.detail
        )
        source = "deterministic"
        ai_status = "FALLBACK"
        if http_exc.status_code == 429:
            ai_notice = "AI service is quota-limited. Displaying safe deterministic briefing based on verified project records."
        else:
            ai_notice = f"AI service unavailable ({http_exc.detail}). Displaying safe deterministic briefing."
        sections = generate_safe_deterministic_briefing(facts)

    except Exception as exc:
        logger.error(
            "Unexpected error querying AI for pulse briefing on project %s: %s",
            project_id, exc
        )
        source = "deterministic"
        ai_status = "FALLBACK"
        ai_notice = "AI response could not be parsed. Displaying safe deterministic briefing."
        sections = generate_safe_deterministic_briefing(facts)

    return ProjectPulseBriefingResponse(
        project_id=facts["project_id"],
        project_name=facts["project_name"],
        generated_at=now_iso,
        data_freshness=freshness_str,
        health_score=facts["health_score"],
        health_data_completeness=facts["health_completeness"],
        sections=sections,
        verified_facts=facts,
        source=source,
        ai_status=ai_status,
        ai_notice=ai_notice,
    )
