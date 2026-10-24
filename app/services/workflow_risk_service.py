"""
Workflow Risk Service — production-grade risk detection, investigation,
corrective-action management, escalation, lifecycle transitions, and audit tracking.
"""
import os
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.user import UserOut
from app.schemas.workflow_risk import (
    RiskLevel,
    RiskStatus,
    RiskComment,
    RiskHistoryEntry,
    WorkflowRiskOut,
    WorkflowRiskMetricsOut,
)

# ---------------------------------------------------------------------------
# Configurable Thresholds
# ---------------------------------------------------------------------------
STUCK_TASK_DAYS = int(os.getenv("RISK_STUCK_TASK_DAYS", "3"))
REVIEW_DELAY_DAYS = int(os.getenv("RISK_REVIEW_DELAY_DAYS", "2"))
WORKLOAD_THRESHOLD = int(os.getenv("RISK_WORKLOAD_THRESHOLD", "5"))
OVERDUE_CRITICAL_DAYS = int(os.getenv("RISK_OVERDUE_CRITICAL_DAYS", "7"))

_STORE_PATH = Path(__file__).resolve().parent.parent / "db" / ".risk_extensions_store.json"


def _db():
    return get_supabase_client()


# ---------------------------------------------------------------------------
# Fallback / Dual Store Persistence for Extended Risk Metadata
# ---------------------------------------------------------------------------

def _load_extensions_store() -> Dict[str, Dict[str, Any]]:
    if _STORE_PATH.exists():
        try:
            with open(_STORE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_extensions_store(store: Dict[str, Dict[str, Any]]) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to persist risk extensions: {e}")


def _get_extension(risk_id: str) -> Dict[str, Any]:
    store = _load_extensions_store()
    return store.get(str(risk_id), {})


def _update_extension(risk_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    store = _load_extensions_store()
    r_id = str(risk_id)
    current = store.get(r_id, {})
    current.update(updates)
    store[r_id] = current
    _save_extensions_store(store)
    return current


def _add_history_entry(risk_id: str, entry: Dict[str, Any]) -> None:
    store = _load_extensions_store()
    r_id = str(risk_id)
    current = store.get(r_id, {})
    hist = current.get("history", [])
    if "id" not in entry:
        entry["id"] = str(uuid.uuid4())
    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    hist.append(entry)
    current["history"] = hist
    store[r_id] = current
    _save_extensions_store(store)


def _add_comment_entry(risk_id: str, comment: Dict[str, Any]) -> None:
    store = _load_extensions_store()
    r_id = str(risk_id)
    current = store.get(r_id, {})
    comments = current.get("comments", [])
    if "id" not in comment:
        comment["id"] = str(uuid.uuid4())
    if "created_at" not in comment:
        comment["created_at"] = datetime.now(timezone.utc).isoformat()
    comments.append(comment)
    current["comments"] = comments
    store[r_id] = current
    _save_extensions_store(store)


# ---------------------------------------------------------------------------
# Automatic Risk Detection Engine
# ---------------------------------------------------------------------------

def detect_and_update_risks():
    """
    Run deterministic detection rules for all open tasks and projects.
    - Detects:
        * Stuck tasks (IN_PROGRESS > STUCK_TASK_DAYS)
        * Review delays (REVIEW > REVIEW_DELAY_DAYS)
        * Overdue tasks (due_date < now, elevated to CRITICAL if > OVERDUE_CRITICAL_DAYS)
        * Workload risk (active tasks > WORKLOAD_THRESHOLD)
        * Project delay (end_date < now and progress < 100)
        * Overdue corrective actions (recovery_deadline < now and not resolved)
    - Idempotent: Updates existing active risks without creating duplicates.
    - Retains historical resolved risks; detects recurrence.
    - Automatically resolves risks whose conditions have genuinely cleared.
    """
    now = datetime.now(timezone.utc)
    active_risks_detected: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # 1. Fetch live operational data from database
    try:
        tasks_resp = _db().table("tasks").select("*, projects(name, project_manager_id), users(name)").execute()
        tasks = tasks_resp.data or []
    except Exception as exc:
        print(f"Warning: Failed to fetch tasks for risk detection: {exc}")
        tasks = []

    try:
        projects_resp = _db().table("projects").select("*").execute()
        projects = projects_resp.data or []
    except Exception as exc:
        print(f"Warning: Failed to fetch projects for risk detection: {exc}")
        projects = []

    try:
        users_resp = _db().table("users").select("id, name, email").execute()
        users = users_resp.data or []
    except Exception as exc:
        print(f"Warning: Failed to fetch users for risk detection: {exc}")
        users = []

    user_map = {u["id"]: u.get("name", "Unknown") for u in users}
    workloads = {u["id"]: 0 for u in users}

    # Inspect all tasks
    for task in tasks:
        status_val = (task.get("status") or "TODO").upper()
        task_id = str(task["id"])
        project_id = str(task.get("project_id")) if task.get("project_id") else None
        assigned_to = str(task.get("assigned_to")) if task.get("assigned_to") else None
        title = task.get("title", "Untitled Task")
        due_date_str = task.get("due_date")

        if assigned_to and status_val in ("TODO", "IN_PROGRESS", "REVIEW"):
            workloads[assigned_to] = workloads.get(assigned_to, 0) + 1

        # A. STUCK_TASK
        if status_val == "IN_PROGRESS":
            started_at_str = task.get("started_at")
            if started_at_str:
                try:
                    started_at = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                    in_progress_days = (now - started_at).days
                    if in_progress_days >= STUCK_TASK_DAYS:
                        key = ("STUCK_TASK", task_id)
                        active_risks_detected[key] = {
                            "risk_type": "STUCK_TASK",
                            "level": RiskLevel.MEDIUM.value,
                            "title": "Stuck Task",
                            "description": f"Task '{title}' has been in progress for {in_progress_days} days without submission or review.",
                            "project_id": project_id,
                            "task_id": task_id,
                            "user_id": assigned_to,
                            "detection_rule": f"IN_PROGRESS >= {STUCK_TASK_DAYS} days",
                            "detection_reason": f"Task remained in IN_PROGRESS state for {in_progress_days} days.",
                            "severity_reason": f"Exceeded {STUCK_TASK_DAYS}-day standard execution threshold without progress update.",
                        }
                except ValueError:
                    pass

        # B. REVIEW_DELAY
        if status_val == "REVIEW":
            review_started_str = task.get("review_started_at")
            if review_started_str:
                try:
                    review_started = datetime.fromisoformat(review_started_str.replace("Z", "+00:00"))
                    delay_days = (now - review_started).days
                    if delay_days >= REVIEW_DELAY_DAYS:
                        key = ("REVIEW_DELAY", task_id)
                        active_risks_detected[key] = {
                            "risk_type": "REVIEW_DELAY",
                            "level": RiskLevel.LOW.value,
                            "title": "Review Delay",
                            "description": f"Task '{title}' has been awaiting review for {delay_days} days.",
                            "project_id": project_id,
                            "task_id": task_id,
                            "user_id": assigned_to,
                            "detection_rule": f"REVIEW >= {REVIEW_DELAY_DAYS} days",
                            "detection_reason": f"Task has been waiting in review queue for {delay_days} days.",
                            "severity_reason": f"Review cycle exceeded {REVIEW_DELAY_DAYS}-day turnaround threshold.",
                        }
                except ValueError:
                    pass

        # C. OVERDUE_TASK
        if status_val != "COMPLETED" and due_date_str:
            try:
                due_date = datetime.strptime(due_date_str.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if now > due_date:
                    overdue_days = (now - due_date).days
                    is_critical = overdue_days >= OVERDUE_CRITICAL_DAYS
                    level = RiskLevel.CRITICAL.value if is_critical else RiskLevel.HIGH.value
                    key = ("OVERDUE_TASK", task_id)
                    active_risks_detected[key] = {
                        "risk_type": "OVERDUE_TASK",
                        "level": level,
                        "title": "Overdue Task",
                        "description": f"Task '{title}' is past its due date ({due_date.strftime('%Y-%m-%d')}) by {overdue_days} day(s).",
                        "project_id": project_id,
                        "task_id": task_id,
                        "user_id": assigned_to,
                        "detection_rule": "due_date < current_timestamp AND status != COMPLETED",
                        "detection_reason": f"Task missed deadline of {due_date.strftime('%Y-%m-%d')} by {overdue_days} day(s).",
                        "severity_reason": (
                            f"Overdue by {overdue_days} days (Critical threshold >= {OVERDUE_CRITICAL_DAYS} days)"
                            if is_critical
                            else f"Overdue by {overdue_days} days (High priority deadline breach)"
                        ),
                    }
            except ValueError:
                pass

    # D. WORKLOAD_RISK
    for user in users:
        uid = str(user["id"])
        cnt = workloads.get(uid, 0)
        if cnt > WORKLOAD_THRESHOLD:
            key = ("WORKLOAD_RISK", uid)
            active_risks_detected[key] = {
                "risk_type": "WORKLOAD_RISK",
                "level": RiskLevel.MEDIUM.value,
                "title": "Workload Risk",
                "description": f"Developer {user_map.get(uid, 'Unknown')} has {cnt} active tasks, exceeding the capacity limit of {WORKLOAD_THRESHOLD}.",
                "project_id": None,
                "task_id": None,
                "user_id": uid,
                "detection_rule": f"active_tasks > {WORKLOAD_THRESHOLD}",
                "detection_reason": f"Developer is currently handling {cnt} simultaneous active tasks.",
                "severity_reason": f"Active workload exceeds safety benchmark of {WORKLOAD_THRESHOLD} concurrent tasks.",
            }

    # E. PROJECT_DELAY
    for project in projects:
        p_status = (project.get("status") or "").upper()
        if p_status not in ("COMPLETED", "ARCHIVED"):
            end_date_str = project.get("end_date")
            progress = project.get("progress", 0) or 0
            if end_date_str:
                try:
                    end_date = datetime.strptime(end_date_str.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if now > end_date and progress < 100:
                        is_crit = progress < 50
                        key = ("PROJECT_DELAY", str(project["id"]))
                        active_risks_detected[key] = {
                            "risk_type": "PROJECT_DELAY",
                            "level": RiskLevel.CRITICAL.value if is_crit else RiskLevel.HIGH.value,
                            "title": "Project Delay",
                            "description": f"Project '{project.get('name')}' is past its target deadline ({end_date.strftime('%Y-%m-%d')}) at {progress}% completion.",
                            "project_id": str(project["id"]),
                            "task_id": None,
                            "user_id": str(project.get("project_manager_id")) if project.get("project_manager_id") else None,
                            "detection_rule": "end_date < current_timestamp AND progress < 100",
                            "detection_reason": f"Project passed schedule target of {end_date.strftime('%Y-%m-%d')} with progress at {progress}%.",
                            "severity_reason": (
                                f"Project deadline breached with critical backlog (progress {progress}% < 50%)"
                                if is_crit
                                else f"Project deadline breached at {progress}% progress"
                            ),
                        }
                except ValueError:
                    pass

    # 2. Reconcile with Database
    try:
        db_risks_resp = _db().table("workflow_risks").select("*").execute()
        all_db_risks = db_risks_resp.data or []

        # Map existing records:
        # active (is_resolved == False) and historical (is_resolved == True)
        active_db_risks: Dict[Tuple[str, str], Dict[str, Any]] = {}
        historical_cleared: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

        for r in all_db_risks:
            risk_id = str(r["id"])
            entity_id = r.get("task_id")
            if not entity_id and r.get("risk_type") == "WORKLOAD_RISK":
                entity_id = r.get("user_id")
            if not entity_id and r.get("risk_type") == "PROJECT_DELAY":
                entity_id = r.get("project_id")
            entity_key = (r["risk_type"], str(entity_id))

            is_res = r.get("is_resolved", False) or r.get("status") == "RESOLVED"
            if not is_res:
                active_db_risks[entity_key] = r
            else:
                historical_cleared.setdefault(entity_key, []).append(r)

        # A. Insert new risks or update recurring status
        to_insert = []
        for key, risk_data in active_risks_detected.items():
            if key not in active_db_risks:
                # Check recurrence
                prev_instances = historical_cleared.get(key, [])
                is_recurring = len(prev_instances) > 0
                recurrence_count = len(prev_instances)

                # Prepare standard Supabase columns (safe for base table)
                base_insert = {
                    "risk_type": risk_data["risk_type"],
                    "level": risk_data["level"],
                    "title": risk_data["title"],
                    "description": risk_data["description"],
                    "project_id": risk_data["project_id"],
                    "task_id": risk_data["task_id"],
                    "user_id": risk_data["user_id"],
                    "status": "OPEN",
                    "is_resolved": False,
                    "detected_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
                to_insert.append((key, base_insert, risk_data, is_recurring, recurrence_count))

        if to_insert:
            inserted_rows = []
            for key, base_data, full_data, is_rec, rec_count in to_insert:
                try:
                    res = _db().table("workflow_risks").insert(base_data).execute()
                    if res.data:
                        new_r = res.data[0]
                        new_id = str(new_r["id"])
                        inserted_rows.append(new_r)

                        # Save extended metadata
                        ext = {
                            "detection_rule": full_data.get("detection_rule"),
                            "detection_reason": full_data.get("detection_reason"),
                            "severity_reason": full_data.get("severity_reason"),
                            "is_recurring": is_rec,
                            "recurrence_count": rec_count,
                            "action_status": "NOT_STARTED",
                            "is_escalated": False,
                            "comments": [],
                            "history": [
                                {
                                    "id": str(uuid.uuid4()),
                                    "timestamp": now.isoformat(),
                                    "user_id": None,
                                    "user_name": "System Detection",
                                    "action": "DETECTED" if not is_rec else "REOPENED_RECURRING",
                                    "details": (
                                        f"Risk auto-detected: {full_data.get('detection_reason')}"
                                        if not is_rec
                                        else f"Recurring risk detected (occurrence #{rec_count + 1}): {full_data.get('detection_reason')}"
                                    ),
                                }
                            ],
                        }
                        _update_extension(new_id, ext)
                except Exception as ins_exc:
                    print(f"Warning: Failed to insert risk {base_data.get('title')}: {ins_exc}")

            # Notify and log activity
            from app.services.activity_service import log_activity
            from app.services.notification_service import NotificationService
            from app.schemas.notification import NotificationType

            for r in inserted_rows:
                desc = f"Workflow Risk detected: {r.get('title')}"
                is_p_delay = r.get("risk_type") == "PROJECT_DELAY"
                log_activity(
                    user_id=r.get("user_id"),
                    action="RISK_DETECTED",
                    entity_type="project" if is_p_delay else "task",
                    entity_id=r.get("project_id") if is_p_delay else r.get("task_id"),
                    description=desc,
                )

                target_user = r.get("user_id")
                if target_user:
                    try:
                        NotificationService.create_notification(
                            recipient_id=str(target_user),
                            type=NotificationType.RISK_ALERT,
                            title=f"Workflow Risk: {r.get('title')}",
                            message=r.get("description", "A workflow risk was detected."),
                            project_id=str(r.get("project_id")) if r.get("project_id") else None,
                            task_id=str(r.get("task_id")) if r.get("task_id") else None,
                        )
                    except Exception as exc:
                        print(f"Warning: Failed to create risk notification: {exc}")

        # B. Check for overdue corrective actions on active risks
        for key, r in active_db_risks.items():
            ext = _get_extension(str(r["id"]))
            rec_deadline = ext.get("recovery_deadline")
            if rec_deadline:
                try:
                    rd_dt = datetime.fromisoformat(rec_deadline.replace("Z", "+00:00"))
                    if now > rd_dt and ext.get("action_status") != "COMPLETED":
                        if not ext.get("is_action_overdue"):
                            _update_extension(str(r["id"]), {
                                "is_action_overdue": True,
                                "action_status": "OVERDUE",
                            })
                            _add_history_entry(str(r["id"]), {
                                "action": "ACTION_OVERDUE",
                                "user_name": "System Sentinel",
                                "details": f"Corrective action recovery deadline ({rec_deadline}) passed without completion.",
                            })
                except Exception:
                    pass

        # C. Resolve risks whose conditions have genuinely cleared
        to_resolve = []
        for key, r in active_db_risks.items():
            if key not in active_risks_detected:
                to_resolve.append(r)

        if to_resolve:
            now_iso = now.isoformat()
            from app.services.activity_service import log_activity
            for r in to_resolve:
                r_id = str(r["id"])
                _db().table("workflow_risks").update({
                    "is_resolved": True,
                    "status": "RESOLVED",
                    "resolved_at": now_iso,
                    "updated_at": now_iso,
                }).eq("id", r_id).execute()

                _update_extension(r_id, {
                    "resolution_notes": "Automatically resolved: underlying condition cleared.",
                    "action_status": "COMPLETED",
                })
                _add_history_entry(r_id, {
                    "action": "AUTO_RESOLVED",
                    "user_name": "System Verifier",
                    "details": "Underlying condition cleared and verified by detection engine.",
                })

                desc = f"Workflow Risk resolved: {r.get('title')}"
                if r.get("risk_type") == "PROJECT_DELAY":
                    log_activity(user_id=r.get("user_id"), action="RISK_RESOLVED", entity_type="project", entity_id=r.get("project_id"), description=desc)
                else:
                    log_activity(user_id=r.get("user_id"), action="RISK_RESOLVED", entity_type="task", entity_id=r.get("task_id"), description=desc)

    except Exception as exc:
        print(f"Error during risk detection/reconciliation: {exc}")


# ---------------------------------------------------------------------------
# Enriched Risk Retrieval Service
# ---------------------------------------------------------------------------

def _hydrate_risk_out(r: Dict[str, Any], ext: Dict[str, Any], task_info: Dict[str, Any], project_info: Dict[str, Any], users_map: Dict[str, Any]) -> WorkflowRiskOut:
    """Combine base DB record, relational links, and extended metadata into WorkflowRiskOut."""
    risk_id = str(r["id"])
    task_id = str(r["task_id"]) if r.get("task_id") else None
    project_id = str(r["project_id"]) if r.get("project_id") else None
    user_id = str(r["user_id"]) if r.get("user_id") else None

    # Calculate overdue duration
    task_due_date = task_info.get("due_date")
    overdue_days = None
    if task_due_date and task_info.get("status") != "COMPLETED":
        try:
            dd = datetime.strptime(task_due_date.split("T")[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now > dd:
                overdue_days = (now - dd).days
        except Exception:
            pass

    # Severity level
    raw_level = r.get("level", "Medium")
    try:
        sev = RiskLevel(raw_level.title())
    except Exception:
        sev = RiskLevel.MEDIUM

    # Status
    raw_status = (r.get("status") or ("RESOLVED" if r.get("is_resolved") else "OPEN")).upper()

    # Action owner / Risk owner
    owner_id = ext.get("owner_id") or user_id
    owner_name = users_map.get(owner_id, {}).get("name") if owner_id else None

    act_owner_id = ext.get("action_owner_id") or owner_id
    act_owner_name = users_map.get(act_owner_id, {}).get("name") if act_owner_id else owner_name

    pm_id = project_info.get("project_manager_id")
    pm_name = users_map.get(str(pm_id), {}).get("name") if pm_id else None

    assignee_id = task_info.get("assigned_to") or user_id
    assignee_name = users_map.get(str(assignee_id), {}).get("name") if assignee_id else None

    # Escalated by name
    esc_by = ext.get("escalated_by")
    esc_by_name = users_map.get(str(esc_by), {}).get("name") if esc_by else None

    # Resolved by name
    res_by = ext.get("resolved_by")
    res_by_name = users_map.get(str(res_by), {}).get("name") if res_by else None

    # Verified by name
    ver_by = ext.get("verified_by")
    ver_by_name = users_map.get(str(ver_by), {}).get("name") if ver_by else None

    comments_raw = ext.get("comments", [])
    history_raw = ext.get("history", [])

    comments = [
        RiskComment(
            id=c.get("id", str(uuid.uuid4())),
            user_id=c.get("user_id", ""),
            user_name=c.get("user_name", "Unknown"),
            user_role=c.get("user_role"),
            content=c.get("content", ""),
            is_blocker=c.get("is_blocker", False),
            evidence_url=c.get("evidence_url"),
            created_at=c.get("created_at", ""),
        )
        for c in comments_raw
    ]

    history = [
        RiskHistoryEntry(
            id=h.get("id", str(uuid.uuid4())),
            timestamp=h.get("timestamp", ""),
            user_id=h.get("user_id"),
            user_name=h.get("user_name"),
            action=h.get("action", "UPDATE"),
            details=h.get("details", ""),
            previous_value=h.get("previous_value"),
            new_value=h.get("new_value"),
        )
        for h in history_raw
    ]

    return WorkflowRiskOut(
        id=risk_id,
        risk_type=r.get("risk_type", ""),
        title=r.get("title", ""),
        description=r.get("description", ""),
        severity=sev,
        project_id=project_id,
        project_name=project_info.get("name"),
        task_id=task_id,
        task_title=task_info.get("title"),
        task_status=task_info.get("status"),
        task_due_date=task_due_date,
        overdue_duration_days=overdue_days,
        assignee_id=str(assignee_id) if assignee_id else None,
        assignee_name=assignee_name,
        project_manager_id=str(pm_id) if pm_id else None,
        project_manager_name=pm_name,
        owner_id=str(owner_id) if owner_id else None,
        owner_name=owner_name,
        detection_rule=ext.get("detection_rule"),
        detection_reason=ext.get("detection_reason") or r.get("description"),
        severity_reason=ext.get("severity_reason"),
        status=raw_status,
        is_resolved=bool(r.get("is_resolved", False) or raw_status == "RESOLVED"),
        detected_at=r.get("detected_at", r.get("created_at", "")),
        resolved_at=r.get("resolved_at"),
        created_at=r.get("created_at", ""),
        updated_at=r.get("updated_at", ""),
        corrective_action_plan=ext.get("corrective_action_plan"),
        recovery_deadline=ext.get("recovery_deadline"),
        action_owner_id=str(act_owner_id) if act_owner_id else None,
        action_owner_name=act_owner_name,
        action_status=ext.get("action_status", "NOT_STARTED"),
        is_action_overdue=bool(ext.get("is_action_overdue", False)),
        is_escalated=bool(ext.get("is_escalated", False)),
        escalated_to=ext.get("escalated_to"),
        escalated_by=str(esc_by) if esc_by else None,
        escalated_by_name=esc_by_name,
        escalated_at=ext.get("escalated_at"),
        escalation_reason=ext.get("escalation_reason"),
        resolution_notes=ext.get("resolution_notes"),
        resolved_by=str(res_by) if res_by else None,
        resolved_by_name=res_by_name,
        verified_by=str(ver_by) if ver_by else None,
        verified_by_name=ver_by_name,
        verification_notes=ext.get("verification_notes"),
        is_recurring=bool(ext.get("is_recurring", False)),
        recurrence_count=int(ext.get("recurrence_count", 0)),
        github_pr_url=task_info.get("github_pr_url"),
        submission_id=task_info.get("submission_id"),
        comments_count=len(comments),
        comments=comments,
        history=history,
    )


def get_workflow_risks_service(
    current_user: UserOut,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    risk_type: Optional[str] = None,
    owner_id: Optional[str] = None,
    is_escalated: Optional[bool] = None,
    search: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> List[WorkflowRiskOut]:
    """
    Get workflow risks for the current user based on RBAC:
    - ADMIN: All organization risks
    - PROJECT_MANAGER: Scoped to their managed projects
    - DEVELOPER: Scoped to their assigned tasks and workload
    """
    try:
        detect_and_update_risks()
    except Exception:
        pass

    try:
        # Base query
        query = _db().table("workflow_risks").select("*")

        # RBAC Filtering
        role_val = current_user.role.value if hasattr(current_user.role, "value") else current_user.role
        if role_val == RoleEnum.DEVELOPER.value:
            query = query.eq("user_id", current_user.id)
        elif role_val == RoleEnum.PROJECT_MANAGER.value:
            pm_projects_resp = _db().table("projects").select("id").eq("project_manager_id", current_user.id).execute()
            pm_project_ids = [str(p["id"]) for p in (pm_projects_resp.data or [])]
            if not pm_project_ids:
                return []
            query = query.in_("project_id", pm_project_ids)

        # Filters
        if project_id:
            query = query.eq("project_id", project_id)

        if status:
            st = status.upper()
            if st == "OPEN":
                query = query.eq("is_resolved", False)
            elif st == "RESOLVED":
                query = query.eq("is_resolved", True)
            elif st in ("IN_PROGRESS", "REOPENED"):
                query = query.eq("status", st)

        if severity:
            query = query.eq("level", severity.title())

        if risk_type:
            query = query.eq("risk_type", risk_type)

        query = query.order("created_at", desc=True)
        resp = query.execute()
        rows = resp.data or []

        # Load relational entities in batch for fast performance
        project_ids = list({str(r["project_id"]) for r in rows if r.get("project_id")})
        task_ids = list({str(r["task_id"]) for r in rows if r.get("task_id")})
        user_ids = list({str(r["user_id"]) for r in rows if r.get("user_id")})

        projects_dict = {}
        if project_ids:
            try:
                p_resp = _db().table("projects").select("id, name, project_manager_id, status, progress, end_date").in_("id", project_ids).execute()
                for p in p_resp.data or []:
                    projects_dict[str(p["id"])] = p
            except Exception:
                pass

        tasks_dict = {}
        if task_ids:
            try:
                t_resp = _db().table("tasks").select("id, title, status, priority, due_date, assigned_to").in_("id", task_ids).execute()
                for t in t_resp.data or []:
                    tasks_dict[str(t["id"])] = t
                    if t.get("assigned_to"):
                        user_ids.append(str(t["assigned_to"]))
            except Exception:
                pass

        # Also get submissions for PR evidence
        if task_ids:
            try:
                sub_resp = _db().table("task_submissions").select("id, task_id, pull_request_url").in_("task_id", task_ids).execute()
                for sub in sub_resp.data or []:
                    tid = str(sub.get("task_id"))
                    if tid in tasks_dict:
                        tasks_dict[tid]["github_pr_url"] = sub.get("pull_request_url")
                        tasks_dict[tid]["submission_id"] = str(sub.get("id"))
            except Exception:
                pass

        users_dict = {}
        unique_user_ids = list(set(user_ids))
        if unique_user_ids:
            try:
                u_resp = _db().table("users").select("id, name, email, role").in_("id", unique_user_ids).execute()
                for u in u_resp.data or []:
                    users_dict[str(u["id"])] = u
            except Exception:
                pass

        # Build results
        results = []
        ext_store = _load_extensions_store()

        for r in rows:
            r_id = str(r["id"])
            ext = ext_store.get(r_id, {})
            p_info = projects_dict.get(str(r.get("project_id")), {})
            t_info = tasks_dict.get(str(r.get("task_id")), {})

            # Filter by owner if requested
            if owner_id and (ext.get("owner_id") or r.get("user_id")) != owner_id:
                continue

            # Filter by escalation
            if is_escalated is not None and bool(ext.get("is_escalated", False)) != is_escalated:
                continue

            hydrated = _hydrate_risk_out(r, ext, t_info, p_info, users_dict)

            # Search query matching
            if search and search.strip():
                s = search.lower().strip()
                matches = (
                    s in hydrated.title.lower()
                    or s in hydrated.description.lower()
                    or s in hydrated.risk_type.lower()
                    or (hydrated.project_name and s in hydrated.project_name.lower())
                    or (hydrated.task_title and s in hydrated.task_title.lower())
                )
                if not matches:
                    continue

            results.append(hydrated)

        # Pagination if specified
        if offset is not None and offset > 0:
            results = results[offset:]
        if limit is not None and limit > 0:
            results = results[:limit]

        return results

    except Exception as exc:
        print(f"Error fetching workflow risks: {exc}")
        return []


def get_workflow_risk_detail_service(risk_id: str, current_user: UserOut) -> WorkflowRiskOut:
    """Fetch complete detail for a single risk with full discussion, evidence, and audit history."""
    db_resp = _db().table("workflow_risks").select("*").eq("id", risk_id).execute()
    rows = db_resp.data or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow risk not found.")

    r = rows[0]
    p_id = str(r.get("project_id")) if r.get("project_id") else None

    # Check permission
    role_val = current_user.role.value if hasattr(current_user.role, "value") else current_user.role
    if role_val == RoleEnum.DEVELOPER.value and str(r.get("user_id")) != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this risk.")
    elif role_val == RoleEnum.PROJECT_MANAGER.value and p_id:
        p_chk = _db().table("projects").select("project_manager_id").eq("id", p_id).execute()
        if p_chk.data and str(p_chk.data[0].get("project_manager_id")) != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to risks outside your managed projects.")

    ext = _get_extension(risk_id)

    # Fetch task and project
    t_info = {}
    if r.get("task_id"):
        t_resp = _db().table("tasks").select("id, title, status, priority, due_date, assigned_to").eq("id", r["task_id"]).execute()
        if t_resp.data:
            t_info = t_resp.data[0]
            # Get PR evidence if available
            sub_resp = _db().table("task_submissions").select("id, pull_request_url").eq("task_id", r["task_id"]).order("created_at", desc=True).limit(1).execute()
            if sub_resp.data:
                t_info["github_pr_url"] = sub_resp.data[0].get("pull_request_url")
                t_info["submission_id"] = str(sub_resp.data[0].get("id"))

    p_info = {}
    if p_id:
        p_resp = _db().table("projects").select("id, name, project_manager_id, status, progress, end_date").eq("id", p_id).execute()
        if p_resp.data:
            p_info = p_resp.data[0]

    # Gather users
    u_ids = [str(uid) for uid in [r.get("user_id"), t_info.get("assigned_to"), p_info.get("project_manager_id"), ext.get("owner_id"), ext.get("action_owner_id")] if uid]
    users_dict = {}
    if u_ids:
        u_resp = _db().table("users").select("id, name, email, role").in_("id", list(set(u_ids))).execute()
        for u in u_resp.data or []:
            users_dict[str(u["id"])] = u

    return _hydrate_risk_out(r, ext, t_info, p_info, users_dict)


# ---------------------------------------------------------------------------
# Corrective Actions, Comments, Escalations, and Lifecycle Operations
# ---------------------------------------------------------------------------

def update_risk_status_service(
    risk_id: str,
    new_status: str,
    current_user: UserOut,
    resolution_notes: Optional[str] = None,
    reason: Optional[str] = None,
) -> WorkflowRiskOut:
    """Enforce valid lifecycle transitions (OPEN -> IN_PROGRESS -> RESOLVED / REOPENED)."""
    detail = get_workflow_risk_detail_service(risk_id, current_user)
    current_status = detail.status.upper()
    target_status = new_status.upper()

    valid_statuses = {"OPEN", "IN_PROGRESS", "RESOLVED", "REOPENED"}
    if target_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{new_status}'. Allowed: {', '.join(valid_statuses)}",
        )

    # Validate transition
    if current_status == "RESOLVED" and target_status not in ("RESOLVED", "REOPENED", "OPEN"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition resolved risk directly to {target_status}. Must reopen first.",
        )

    if target_status == "RESOLVED" and not resolution_notes and not detail.resolution_notes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolution notes are required when marking a risk as RESOLVED.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    is_res = target_status == "RESOLVED"

    # Update Supabase base table
    update_data: Dict[str, Any] = {
        "status": target_status,
        "is_resolved": is_res,
        "updated_at": now_iso,
    }
    if is_res:
        update_data["resolved_at"] = now_iso
    elif target_status in ("OPEN", "IN_PROGRESS", "REOPENED"):
        update_data["resolved_at"] = None

    _db().table("workflow_risks").update(update_data).eq("id", risk_id).execute()

    # Update extensions
    ext_updates: Dict[str, Any] = {}
    if is_res:
        ext_updates["resolution_notes"] = resolution_notes or "Marked as resolved."
        ext_updates["resolved_by"] = current_user.id
        ext_updates["action_status"] = "COMPLETED"
    elif target_status == "REOPENED":
        ext_updates["is_recurring"] = True
        ext_updates["recurrence_count"] = detail.recurrence_count + 1

    _update_extension(risk_id, ext_updates)

    # Record history
    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": f"STATUS_{target_status}",
        "details": reason or resolution_notes or f"Status changed from {current_status} to {target_status}",
        "previous_value": current_status,
        "new_value": target_status,
    })

    # Log activity & notify
    from app.services.activity_service import log_activity
    from app.services.notification_service import NotificationService
    from app.schemas.notification import NotificationType

    log_activity(
        user_id=current_user.id,
        action=f"RISK_STATUS_{target_status}",
        entity_type="workflow_risk",
        entity_id=risk_id,
        project_id=detail.project_id,
        description=f"Workflow risk '{detail.title}' status updated to {target_status}",
    )

    if detail.assignee_id and detail.assignee_id != current_user.id:
        try:
            NotificationService.create_notification(
                recipient_id=detail.assignee_id,
                type=NotificationType.RISK_ALERT,
                title=f"Risk Status: {detail.title} -> {target_status}",
                message=f"Risk status was updated to {target_status} by {current_user.name or 'a team lead'}.",
                project_id=detail.project_id,
                task_id=detail.task_id,
            )
        except Exception:
            pass

    return get_workflow_risk_detail_service(risk_id, current_user)


def set_corrective_action_plan_service(
    risk_id: str,
    action_plan: str,
    recovery_deadline: Optional[str],
    action_owner_id: Optional[str],
    current_user: UserOut,
) -> WorkflowRiskOut:
    """Record a corrective action plan and recovery deadline."""
    if not action_plan or not action_plan.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Corrective action plan cannot be empty.")

    detail = get_workflow_risk_detail_service(risk_id, current_user)

    target_owner = action_owner_id or detail.action_owner_id or detail.assignee_id or current_user.id

    now_iso = datetime.now(timezone.utc).isoformat()
    ext_updates = {
        "corrective_action_plan": action_plan.strip(),
        "recovery_deadline": recovery_deadline,
        "action_owner_id": target_owner,
        "action_status": "IN_PROGRESS",
        "is_action_overdue": False,
    }
    _update_extension(risk_id, ext_updates)

    # If currently OPEN, move to IN_PROGRESS
    if detail.status == "OPEN":
        _db().table("workflow_risks").update({"status": "IN_PROGRESS", "updated_at": now_iso}).eq("id", risk_id).execute()

    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": "ACTION_PLAN_CREATED",
        "details": f"Corrective action plan established: {action_plan[:80]}... (Recovery target: {recovery_deadline or 'None'})",
        "new_value": action_plan,
    })

    # Dispatch notification to action owner
    if target_owner and target_owner != current_user.id:
        from app.services.notification_service import NotificationService
        from app.schemas.notification import NotificationType
        try:
            NotificationService.create_notification(
                recipient_id=target_owner,
                type=NotificationType.RISK_ALERT,
                title=f"Corrective Action Assigned: {detail.title}",
                message=f"You were assigned a corrective action: {action_plan[:100]}",
                project_id=detail.project_id,
                task_id=detail.task_id,
            )
        except Exception:
            pass

    return get_workflow_risk_detail_service(risk_id, current_user)


def add_risk_comment_service(
    risk_id: str,
    content: str,
    is_blocker: bool,
    evidence_url: Optional[str],
    current_user: UserOut,
) -> WorkflowRiskOut:
    """Add a progress update, comment, blocker alert, or supporting evidence."""
    if not content or not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment content cannot be empty.")

    detail = get_workflow_risk_detail_service(risk_id, current_user)
    role_val = current_user.role.value if hasattr(current_user.role, "value") else current_user.role

    now_iso = datetime.now(timezone.utc).isoformat()
    comment = {
        "id": str(uuid.uuid4()),
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "user_role": role_val,
        "content": content.strip(),
        "is_blocker": is_blocker,
        "evidence_url": evidence_url.strip() if evidence_url else None,
        "created_at": now_iso,
    }
    _add_comment_entry(risk_id, comment)

    action_label = "BLOCKER_FLAGGED" if is_blocker else "PROGRESS_UPDATE"
    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": action_label,
        "details": f"{'BLOCKER: ' if is_blocker else ''}{content[:80]}",
    })

    # If blocker flagged, alert Project Manager or Admin
    if is_blocker:
        from app.services.notification_service import NotificationService
        from app.schemas.notification import NotificationType

        pm_id = detail.project_manager_id
        if pm_id and pm_id != current_user.id:
            try:
                NotificationService.create_notification(
                    recipient_id=pm_id,
                    type=NotificationType.RISK_ALERT,
                    title=f"Blocker Alert on Risk: {detail.title}",
                    message=f"{current_user.name or 'A developer'} flagged a blocker: {content[:100]}",
                    project_id=detail.project_id,
                    task_id=detail.task_id,
                )
            except Exception:
                pass

    return get_workflow_risk_detail_service(risk_id, current_user)


def escalate_risk_service(
    risk_id: str,
    escalate_to: str,
    reason: str,
    current_user: UserOut,
) -> WorkflowRiskOut:
    """Escalate a workflow risk to Project Manager or Admin."""
    if not reason or not reason.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Escalation reason is required.")

    detail = get_workflow_risk_detail_service(risk_id, current_user)
    target = escalate_to.upper()
    if target not in ("PROJECT_MANAGER", "ADMIN"):
        target = "PROJECT_MANAGER"

    now_iso = datetime.now(timezone.utc).isoformat()

    # Elevate severity to High or Critical upon escalation
    new_level = RiskLevel.CRITICAL.value if target == "ADMIN" else RiskLevel.HIGH.value
    _db().table("workflow_risks").update({
        "level": new_level,
        "updated_at": now_iso,
    }).eq("id", risk_id).execute()

    _update_extension(risk_id, {
        "is_escalated": True,
        "escalated_to": target,
        "escalated_by": current_user.id,
        "escalated_at": now_iso,
        "escalation_reason": reason.strip(),
    })

    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": "ESCALATED",
        "details": f"Escalated to {target} by {current_user.name}: {reason}",
    })

    # Notify PM or Admin
    from app.services.notification_service import NotificationService
    from app.schemas.notification import NotificationType

    target_user_id = None
    if target == "PROJECT_MANAGER" and detail.project_manager_id:
        target_user_id = detail.project_manager_id
    elif target == "ADMIN":
        # Find first active admin
        try:
            admin_resp = _db().table("users").select("id").eq("role", "admin").limit(1).execute()
            if admin_resp.data:
                target_user_id = str(admin_resp.data[0]["id"])
        except Exception:
            pass

    if target_user_id and target_user_id != current_user.id:
        try:
            NotificationService.create_notification(
                recipient_id=target_user_id,
                type=NotificationType.RISK_ALERT,
                title=f"URGENT: Risk Escalated to {target}: {detail.title}",
                message=f"{current_user.name} escalated this risk: {reason}",
                project_id=detail.project_id,
                task_id=detail.task_id,
            )
        except Exception:
            pass

    return get_workflow_risk_detail_service(risk_id, current_user)


def resolve_risk_service(
    risk_id: str,
    resolution_notes: str,
    verify: bool,
    verification_notes: Optional[str],
    current_user: UserOut,
) -> WorkflowRiskOut:
    """Explicitly resolve a workflow risk with resolution notes and verification sign-off."""
    if not resolution_notes or not resolution_notes.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Resolution notes are required.")

    detail = get_workflow_risk_detail_service(risk_id, current_user)
    role_val = current_user.role.value if hasattr(current_user.role, "value") else current_user.role

    # Verification sign-off requires PM or Admin role
    if verify and role_val not in (RoleEnum.ADMIN.value, RoleEnum.PROJECT_MANAGER.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Project Managers or Admins can perform formal verification sign-off.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    _db().table("workflow_risks").update({
        "status": "RESOLVED",
        "is_resolved": True,
        "resolved_at": now_iso,
        "updated_at": now_iso,
    }).eq("id", risk_id).execute()

    ext_updates: Dict[str, Any] = {
        "resolution_notes": resolution_notes.strip(),
        "resolved_by": current_user.id,
        "action_status": "COMPLETED",
    }
    if verify:
        ext_updates["verified_by"] = current_user.id
        ext_updates["verification_notes"] = verification_notes or "Resolution verified and signed off."

    _update_extension(risk_id, ext_updates)

    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": "RESOLVED_VERIFIED" if verify else "RESOLVED",
        "details": f"Resolved: {resolution_notes}" + (f" (Verified: {verification_notes})" if verify else ""),
    })

    return get_workflow_risk_detail_service(risk_id, current_user)


def reopen_risk_service(
    risk_id: str,
    reason: str,
    current_user: UserOut,
) -> WorkflowRiskOut:
    """Reopen a previously resolved workflow risk."""
    if not reason or not reason.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reopening reason is required.")

    detail = get_workflow_risk_detail_service(risk_id, current_user)
    now_iso = datetime.now(timezone.utc).isoformat()

    _db().table("workflow_risks").update({
        "status": "REOPENED",
        "is_resolved": False,
        "resolved_at": None,
        "updated_at": now_iso,
    }).eq("id", risk_id).execute()

    _update_extension(risk_id, {
        "is_recurring": True,
        "recurrence_count": detail.recurrence_count + 1,
        "action_status": "IN_PROGRESS",
    })

    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": "REOPENED",
        "details": f"Risk reopened by {current_user.name}: {reason}",
    })

    return get_workflow_risk_detail_service(risk_id, current_user)


def assign_risk_owner_service(
    risk_id: str,
    owner_id: str,
    current_user: UserOut,
) -> WorkflowRiskOut:
    """Assign or reassign ownership of a workflow risk."""
    if not owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_id is required.")

    # Validate target user exists
    u_chk = _db().table("users").select("id, name, email").eq("id", owner_id).execute()
    if not u_chk.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found.")

    target_user_name = u_chk.data[0].get("name", "Unknown")
    detail = get_workflow_risk_detail_service(risk_id, current_user)

    _update_extension(risk_id, {
        "owner_id": owner_id,
        "action_owner_id": owner_id,
    })

    _add_history_entry(risk_id, {
        "user_id": current_user.id,
        "user_name": current_user.name or current_user.email,
        "action": "OWNER_ASSIGNED",
        "details": f"Assigned risk ownership to {target_user_name}",
        "new_value": owner_id,
    })

    # Notify new owner
    if owner_id != current_user.id:
        from app.services.notification_service import NotificationService
        from app.schemas.notification import NotificationType
        try:
            NotificationService.create_notification(
                recipient_id=owner_id,
                type=NotificationType.RISK_ALERT,
                title=f"Assigned Workflow Risk: {detail.title}",
                message=f"You have been assigned ownership of the risk '{detail.title}' by {current_user.name}.",
                project_id=detail.project_id,
                task_id=detail.task_id,
            )
        except Exception:
            pass

    return get_workflow_risk_detail_service(risk_id, current_user)


# ---------------------------------------------------------------------------
# Metrics Summary Service
# ---------------------------------------------------------------------------

def get_workflow_risk_metrics_service(current_user: UserOut) -> WorkflowRiskMetricsOut:
    """Compute accurate, live aggregated risk metrics scoped by user permissions."""
    all_risks = get_workflow_risks_service(current_user)

    metrics = WorkflowRiskMetricsOut(
        total_risks=len(all_risks),
        open_risks=sum(1 for r in all_risks if r.status == "OPEN"),
        in_progress_risks=sum(1 for r in all_risks if r.status == "IN_PROGRESS"),
        resolved_risks=sum(1 for r in all_risks if r.status == "RESOLVED" or r.is_resolved),
        reopened_risks=sum(1 for r in all_risks if r.status == "REOPENED"),
        critical_count=sum(1 for r in all_risks if r.severity == RiskLevel.CRITICAL and not r.is_resolved),
        high_count=sum(1 for r in all_risks if r.severity == RiskLevel.HIGH and not r.is_resolved),
        medium_count=sum(1 for r in all_risks if r.severity == RiskLevel.MEDIUM and not r.is_resolved),
        low_count=sum(1 for r in all_risks if r.severity == RiskLevel.LOW and not r.is_resolved),
        overdue_tasks_count=sum(1 for r in all_risks if r.risk_type == "OVERDUE_TASK" and not r.is_resolved),
        review_delays_count=sum(1 for r in all_risks if r.risk_type == "REVIEW_DELAY" and not r.is_resolved),
        stuck_tasks_count=sum(1 for r in all_risks if r.risk_type == "STUCK_TASK" and not r.is_resolved),
        project_delays_count=sum(1 for r in all_risks if r.risk_type == "PROJECT_DELAY" and not r.is_resolved),
        workload_risks_count=sum(1 for r in all_risks if r.risk_type == "WORKLOAD_RISK" and not r.is_resolved),
        overdue_actions_count=sum(1 for r in all_risks if r.is_action_overdue and not r.is_resolved),
        escalated_count=sum(1 for r in all_risks if r.is_escalated and not r.is_resolved),
        recurring_count=sum(1 for r in all_risks if r.is_recurring),
    )
    return metrics
