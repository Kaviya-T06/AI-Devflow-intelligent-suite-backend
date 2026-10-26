"""
Notification Service — manages persistence, retrieval, deduplication,
and notification generation for user events.
"""
import json
import os
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from uuid import uuid4

from fastapi import HTTPException, status
from postgrest.exceptions import APIError

from app.db.supabase_client import get_supabase_client
from app.schemas.notification import NotificationCreate, NotificationOut, NotificationType

_STORE_PATH = Path(__file__).resolve().parent.parent / "db" / ".notifications_store.json"


def _load_fallback_store() -> Dict[str, Dict[str, Any]]:
    if _STORE_PATH.exists():
        try:
            with open(_STORE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_fallback_store(store: Dict[str, Dict[str, Any]]) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to persist fallback notifications: {e}")


# Persistent fallback store for when public.notifications table is not yet migrated in Supabase
_fallback_notifications: Dict[str, Dict[str, Any]] = _load_fallback_store()
_project_name_cache: Dict[str, str] = {}
_user_name_cache: Dict[str, str] = {}


def _is_table_missing_error(exc: Exception) -> bool:
    """Check if API exception is PGRST205 (table missing from schema cache)."""
    err_str = str(exc)
    return "PGRST205" in err_str or "public.notifications" in err_str or "table 'public.notifications'" in err_str.lower()


class NotificationService:
    @staticmethod
    def create_notification(
        recipient_id: str,
        type: NotificationType,
        title: str,
        message: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> NotificationOut:
        """
        Create a new notification for a recipient.
        Deduplicates identical notifications generated within 60 seconds.
        """
        if not recipient_id or not title or not message:
            raise ValueError("recipient_id, title, and message are required.")

        db = get_supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        type_str = type.value if isinstance(type, NotificationType) else type

        # Check Do Not Disturb status for recipient
        from app.services.presence_service import PresenceService
        if PresenceService.is_dnd_active(recipient_id):
            critical_types = {NotificationType.RISK_ALERT, NotificationType.TASK_OVERDUE}
            is_critical = (
                type in critical_types
                or 'urgent' in title.lower()
                or 'critical' in title.lower()
            )
            if not is_critical:
                # User is in Do Not Disturb: suppress non-urgent notifications from alerting
                return NotificationOut(
                    id=str(uuid4()),
                    recipient_id=recipient_id,
                    type=NotificationType(type_str),
                    title=f'[Muted - DND] {title}',
                    message=message,
                    project_id=project_id,
                    task_id=task_id,
                    is_read=True,
                    read_at=now_iso,
                    created_at=now_iso,
                )

        # Deduplication check: check if an identical notification was recently created
        try:
            query = (
                db.table("notifications")
                .select("id, created_at")
                .eq("recipient_id", recipient_id)
                .eq("type", type_str)
                .eq("title", title)
                .order("created_at", desc=True)
                .limit(1)
            )
            if task_id:
                query = query.eq("task_id", task_id)
            else:
                query = query.is_("task_id", "null")
            if project_id:
                query = query.eq("project_id", project_id)
            else:
                query = query.is_("project_id", "null")

            resp = query.execute()
            if resp.data:
                latest = resp.data[0]
                created_dt = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                if (now_dt - created_dt).total_seconds() < 60:
                    return NotificationService._get_by_id(latest["id"], recipient_id)
        except Exception as exc:
            if _is_table_missing_error(exc):
                # Deduplication check against fallback memory store
                target_task = str(task_id) if task_id else None
                target_proj = str(project_id) if project_id else None
                recent = [
                    v for v in _fallback_notifications.values()
                    if v["recipient_id"] == recipient_id
                    and v["type"] == type_str
                    and v["title"] == title
                    and (str(v.get("task_id")) if v.get("task_id") else None) == target_task
                    and (str(v.get("project_id")) if v.get("project_id") else None) == target_proj
                ]
                if recent:
                    recent.sort(key=lambda x: x["created_at"], reverse=True)
                    latest = recent[0]
                    created_dt = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
                    now_dt = datetime.now(timezone.utc)
                    if (now_dt - created_dt).total_seconds() < 60:
                        return NotificationService._get_by_id(latest["id"], recipient_id)

        new_id = str(uuid4())
        type_str = type.value if isinstance(type, NotificationType) else type
        payload = {
            "id": new_id,
            "recipient_id": recipient_id,
            "type": type_str,
            "title": title,
            "message": message,
            "project_id": project_id,
            "task_id": task_id,
            "is_read": False,
            "read_at": None,
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        try:
            resp = db.table("notifications").insert(payload).execute()
            if resp.data and len(resp.data) > 0:
                row = resp.data[0]
                return NotificationOut(
                    id=str(row["id"]),
                    recipient_id=str(row["recipient_id"]),
                    type=NotificationType(row["type"]),
                    title=row["title"],
                    message=row["message"],
                    project_id=str(row["project_id"]) if row.get("project_id") else None,
                    task_id=str(row["task_id"]) if row.get("task_id") else None,
                    is_read=bool(row.get("is_read", False)),
                    read_at=str(row["read_at"]) if row.get("read_at") else None,
                    created_at=str(row["created_at"]),
                )
        except Exception as exc:
            if _is_table_missing_error(exc):
                # Save to persistent fallback store
                _fallback_notifications[new_id] = payload
                _save_fallback_store(_fallback_notifications)
                return NotificationOut(
                    id=new_id,
                    recipient_id=recipient_id,
                    type=NotificationType(type_str),
                    title=title,
                    message=message,
                    project_id=project_id,
                    task_id=task_id,
                    is_read=False,
                    read_at=None,
                    created_at=now_iso,
                )
            raise exc

        return NotificationOut(
            id=new_id,
            recipient_id=recipient_id,
            type=NotificationType(type_str),
            title=title,
            message=message,
            project_id=project_id,
            task_id=task_id,
            is_read=False,
            read_at=None,
            created_at=now_iso,
        )

    @staticmethod
    def _get_by_id(notification_id: str, recipient_id: str) -> NotificationOut:
        """Fetch notification by ID enforcing recipient_id match."""
        db = get_supabase_client()
        try:
            resp = (
                db.table("notifications")
                .select("*")
                .eq("id", notification_id)
                .eq("recipient_id", recipient_id)
                .execute()
            )
            if resp.data:
                row = resp.data[0]
                item = NotificationOut(
                    id=str(row["id"]),
                    recipient_id=str(row["recipient_id"]),
                    type=NotificationType(row["type"]),
                    title=row["title"],
                    message=row["message"],
                    project_id=str(row["project_id"]) if row.get("project_id") else None,
                    task_id=str(row["task_id"]) if row.get("task_id") else None,
                    is_read=bool(row.get("is_read", False)),
                    read_at=str(row["read_at"]) if row.get("read_at") else None,
                    created_at=str(row["created_at"]),
                )
                return NotificationService._enrich_notifications([item])[0]
        except Exception as exc:
            if _is_table_missing_error(exc) and notification_id in _fallback_notifications:
                row = _fallback_notifications[notification_id]
                if row["recipient_id"] == recipient_id:
                    item = NotificationOut(
                        id=row["id"],
                        recipient_id=row["recipient_id"],
                        type=NotificationType(row["type"]),
                        title=row["title"],
                        message=row["message"],
                        project_id=row.get("project_id"),
                        task_id=row.get("task_id"),
                        is_read=row.get("is_read", False),
                        read_at=row.get("read_at"),
                        created_at=row["created_at"],
                    )
                    return NotificationService._enrich_notifications([item])[0]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or access denied.",
        )

    @staticmethod
    def _enrich_notifications(items: List[NotificationOut]) -> List[NotificationOut]:
        """
        Enrich overdue notifications with real project name and task relationship data from database.
        Ensures consistent title: 'Overdue Task — {project_name}' or 'Overdue Task — Project name unavailable'.
        """
        if not items:
            return items

        overdue_items = [
            item for item in items
            if (item.type == NotificationType.TASK_OVERDUE or str(item.type) == "TASK_OVERDUE")
        ]
        if not overdue_items:
            return items

        db = get_supabase_client()
        today_d = date.today()

        # Step 1: Collect task IDs and query tasks table
        task_ids = list({str(it.task_id) for it in overdue_items if it.task_id})
        tasks_map = {}
        if task_ids:
            try:
                t_resp = db.table("tasks").select("id, title, project_id, assigned_to, due_date").in_("id", task_ids).execute()
                for t in (t_resp.data or []):
                    tasks_map[str(t["id"])] = t
            except Exception as t_err:
                print(f"Warning: Failed to fetch tasks for overdue enrichment: {t_err}")

        # Step 2: Collect project IDs from notification items + fetched tasks
        proj_ids = set()
        for it in overdue_items:
            if it.project_id:
                proj_ids.add(str(it.project_id))
            elif it.task_id and str(it.task_id) in tasks_map:
                t_proj = tasks_map[str(it.task_id)].get("project_id")
                if t_proj:
                    proj_ids.add(str(t_proj))

        # Check in-memory cache first for projects
        missing_proj_ids = [pid for pid in proj_ids if pid not in _project_name_cache]
        if missing_proj_ids:
            try:
                p_resp = db.table("projects").select("id, name").in_("id", missing_proj_ids).execute()
                for p in (p_resp.data or []):
                    _project_name_cache[str(p["id"])] = p.get("name")
            except Exception as p_err:
                print(f"Warning: Failed to fetch projects for overdue enrichment: {p_err}")

        # Step 3: Collect assigned developer IDs from tasks
        dev_ids = set()
        for t in tasks_map.values():
            if t.get("assigned_to"):
                dev_ids.add(str(t["assigned_to"]))

        # Check in-memory cache first for users
        missing_dev_ids = [uid for uid in dev_ids if uid not in _user_name_cache]
        if missing_dev_ids:
            try:
                u_resp = db.table("users").select("id, name").in_("id", missing_dev_ids).execute()
                for u in (u_resp.data or []):
                    _user_name_cache[str(u["id"])] = u.get("name")
            except Exception as u_err:
                print(f"Warning: Failed to fetch users for overdue enrichment: {u_err}")

        # Step 4: Populate each overdue item
        for item in overdue_items:
            task_info = tasks_map.get(str(item.task_id)) if item.task_id else None
            if task_info:
                if not item.task_title:
                    item.task_title = task_info.get("title")
                if not item.project_id and task_info.get("project_id"):
                    item.project_id = str(task_info["project_id"])
                if not item.due_date and task_info.get("due_date"):
                    item.due_date = str(task_info["due_date"])
                if not item.assigned_to_name and task_info.get("assigned_to"):
                    item.assigned_to_name = _user_name_cache.get(str(task_info["assigned_to"]))

            # Calculate days overdue from due date
            if item.due_date:
                try:
                    due_d = datetime.fromisoformat(str(item.due_date)[:10]).date()
                    item.days_overdue = max(0, (today_d - due_d).days)
                except Exception:
                    pass

            # Resolve project name through database relationship
            project_name = None
            if item.project_id and str(item.project_id) in _project_name_cache:
                project_name = _project_name_cache[str(item.project_id)]

            item.project_name = project_name

            # Consistent title: "Overdue Task — {project_name}" or "Overdue Task — Project name unavailable"
            if project_name:
                item.title = f"Overdue Task — {project_name}"
            else:
                item.title = "Overdue Task — Project name unavailable"

        return items

    @staticmethod
    def list_user_notifications(
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
    ) -> Tuple[List[NotificationOut], int, int]:
        """
        List notifications belonging strictly to user_id.
        Supports status_filter: 'all', 'unread', 'read'.
        Returns (items, unread_count, total_count).
        """
        db = get_supabase_client()
        try:
            # Query items with optional read status filter
            query = (
                db.table("notifications")
                .select("*")
                .eq("recipient_id", user_id)
            )
            if status_filter:
                sf = status_filter.lower().strip()
                if sf == "unread":
                    query = query.eq("is_read", False)
                elif sf == "read":
                    query = query.eq("is_read", True)

            resp = (
                query
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            rows = resp.data or []

            items = [
                NotificationOut(
                    id=str(r["id"]),
                    recipient_id=str(r["recipient_id"]),
                    type=NotificationType(r["type"]),
                    title=r["title"],
                    message=r["message"],
                    project_id=str(r["project_id"]) if r.get("project_id") else None,
                    task_id=str(r["task_id"]) if r.get("task_id") else None,
                    is_read=bool(r.get("is_read", False)),
                    read_at=str(r["read_at"]) if r.get("read_at") else None,
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]
            items = NotificationService._enrich_notifications(items)

            # Unread count (always reflects true unread count for user)
            unread_resp = (
                db.table("notifications")
                .select("id", count="exact")
                .eq("recipient_id", user_id)
                .eq("is_read", False)
                .execute()
            )
            unread_count = unread_resp.count if unread_resp.count is not None else len([i for i in items if not i.is_read])

            # Total count (for pagination)
            total_query = db.table("notifications").select("id", count="exact").eq("recipient_id", user_id)
            if status_filter:
                sf = status_filter.lower().strip()
                if sf == "unread":
                    total_query = total_query.eq("is_read", False)
                elif sf == "read":
                    total_query = total_query.eq("is_read", True)

            total_resp = total_query.execute()
            total_count = total_resp.count if total_resp.count is not None else len(items)

            return items, unread_count, total_count

        except Exception as exc:
            if _is_table_missing_error(exc):
                user_items = [
                    v for v in _fallback_notifications.values()
                    if v["recipient_id"] == user_id
                ]
                user_items.sort(key=lambda x: x["created_at"], reverse=True)

                unread_count = len([r for r in user_items if not r.get("is_read", False)])

                if status_filter:
                    sf = status_filter.lower().strip()
                    if sf == "unread":
                        filtered_items = [r for r in user_items if not r.get("is_read", False)]
                    elif sf == "read":
                        filtered_items = [r for r in user_items if r.get("is_read", False)]
                    else:
                        filtered_items = user_items
                else:
                    filtered_items = user_items

                paginated = filtered_items[offset : offset + limit]
                items = [
                    NotificationOut(
                        id=r["id"],
                        recipient_id=r["recipient_id"],
                        type=NotificationType(r["type"]),
                        title=r["title"],
                        message=r["message"],
                        project_id=r.get("project_id"),
                        task_id=r.get("task_id"),
                        is_read=r.get("is_read", False),
                        read_at=r.get("read_at"),
                        created_at=r["created_at"],
                    )
                    for r in paginated
                ]
                items = NotificationService._enrich_notifications(items)
                return items, unread_count, len(filtered_items)
            raise exc

    @staticmethod
    def get_unread_count(user_id: str) -> int:
        """Return the unread notification count for user_id."""
        db = get_supabase_client()
        try:
            resp = (
                db.table("notifications")
                .select("id", count="exact")
                .eq("recipient_id", user_id)
                .eq("is_read", False)
                .execute()
            )
            return resp.count if resp.count is not None else 0
        except Exception as exc:
            if _is_table_missing_error(exc):
                return len([
                    v for v in _fallback_notifications.values()
                    if v["recipient_id"] == user_id and not v.get("is_read", False)
                ])
            return 0

    @staticmethod
    def mark_as_read(user_id: str, notification_id: str) -> NotificationOut:
        """Mark a notification as read if owned by user_id."""
        db = get_supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            # First verify ownership
            check_resp = (
                db.table("notifications")
                .select("recipient_id")
                .eq("id", notification_id)
                .execute()
            )
            if not check_resp.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Notification not found.",
                )
            if str(check_resp.data[0]["recipient_id"]) != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: Cannot modify another user's notification.",
                )

            db.table("notifications").update(
                {"is_read": True, "read_at": now_iso, "updated_at": now_iso}
            ).eq("id", notification_id).execute()

            return NotificationService._get_by_id(notification_id, user_id)
        except HTTPException:
            raise
        except Exception as exc:
            if _is_table_missing_error(exc):
                if notification_id not in _fallback_notifications:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Notification not found.",
                    )
                item = _fallback_notifications[notification_id]
                if item["recipient_id"] != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: Cannot modify another user's notification.",
                    )
                item["is_read"] = True
                item["read_at"] = now_iso
                item["updated_at"] = now_iso
                _save_fallback_store(_fallback_notifications)
                return NotificationOut(
                    id=item["id"],
                    recipient_id=item["recipient_id"],
                    type=NotificationType(item["type"]),
                    title=item["title"],
                    message=item["message"],
                    project_id=item.get("project_id"),
                    task_id=item.get("task_id"),
                    is_read=True,
                    read_at=now_iso,
                    created_at=item["created_at"],
                )
            raise exc

    @staticmethod
    def mark_all_as_read(user_id: str) -> int:
        """Mark all notifications as read for user_id."""
        db = get_supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            resp = (
                db.table("notifications")
                .update({"is_read": True, "read_at": now_iso, "updated_at": now_iso})
                .eq("recipient_id", user_id)
                .eq("is_read", False)
                .execute()
            )
            return len(resp.data) if resp.data else 0
        except Exception as exc:
            if _is_table_missing_error(exc):
                updated_count = 0
                for item in _fallback_notifications.values():
                    if item["recipient_id"] == user_id and not item.get("is_read", False):
                        item["is_read"] = True
                        item["read_at"] = now_iso
                        item["updated_at"] = now_iso
                        updated_count += 1
                if updated_count > 0:
                    _save_fallback_store(_fallback_notifications)
                return updated_count
            raise exc

    @staticmethod
    def delete_notification(user_id: str, notification_id: str) -> bool:
        """Delete/dismiss notification owned by user_id."""
        db = get_supabase_client()
        try:
            check_resp = (
                db.table("notifications")
                .select("recipient_id")
                .eq("id", notification_id)
                .execute()
            )
            if not check_resp.data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Notification not found.",
                )
            if str(check_resp.data[0]["recipient_id"]) != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: Cannot delete another user's notification.",
                )

            db.table("notifications").delete().eq("id", notification_id).execute()
            return True
        except HTTPException:
            raise
        except Exception as exc:
            if _is_table_missing_error(exc):
                if notification_id not in _fallback_notifications:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Notification not found.",
                    )
                item = _fallback_notifications[notification_id]
                if item["recipient_id"] != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: Cannot delete another user's notification.",
                    )
                del _fallback_notifications[notification_id]
                _save_fallback_store(_fallback_notifications)
                return True
            raise exc

    @staticmethod
    def check_and_create_overdue_notifications() -> int:
        """
        Check for tasks with due_date < today that are not COMPLETED.
        Generate TASK_OVERDUE notifications for assignees and project managers.
        Retrieves project name and assigned developer through database relationships.
        Consistent title: 'Overdue Task — {project_name}' or 'Overdue Task — Project name unavailable'.
        """
        db = get_supabase_client()
        today_d = date.today()
        today_str = today_d.isoformat()
        created_count = 0

        try:
            # Query overdue tasks
            tasks_resp = (
                db.table("tasks")
                .select("id, title, assigned_to, project_id, due_date, status")
                .lt("due_date", today_str)
                .neq("status", "COMPLETED")
                .execute()
            )
            overdue_tasks = tasks_resp.data or []

            # Pre-fetch projects for all overdue tasks
            proj_ids = list({str(t["project_id"]) for t in overdue_tasks if t.get("project_id")})
            projects_map = {}
            if proj_ids:
                try:
                    p_resp = db.table("projects").select("id, name, project_manager_id").in_("id", proj_ids).execute()
                    for p in (p_resp.data or []):
                        projects_map[str(p["id"])] = p
                except Exception as p_err:
                    print(f"Warning: Failed to fetch projects: {p_err}")

            # Pre-fetch developer user names
            assigned_ids = list({str(t["assigned_to"]) for t in overdue_tasks if t.get("assigned_to")})
            users_map = {}
            if assigned_ids:
                try:
                    u_resp = db.table("users").select("id, name").in_("id", assigned_ids).execute()
                    for u in (u_resp.data or []):
                        users_map[str(u["id"])] = u.get("name")
                except Exception as u_err:
                    print(f"Warning: Failed to fetch users: {u_err}")

            for task in overdue_tasks:
                task_id = str(task["id"])
                task_title = task.get("title") or "Untitled Task"
                assigned_to = str(task["assigned_to"]) if task.get("assigned_to") else None
                project_id = str(task["project_id"]) if task.get("project_id") else None
                due_date_str = str(task.get("due_date")) if task.get("due_date") else None

                # Calculate days overdue from actual due date and current date
                days_overdue = 0
                if due_date_str:
                    try:
                        due_d = datetime.fromisoformat(due_date_str[:10]).date()
                        days_overdue = max(0, (today_d - due_d).days)
                    except Exception:
                        pass
                days_overdue_str = f"{days_overdue} day{'s' if days_overdue != 1 else ''} overdue"

                # Look up project info
                proj_info = projects_map.get(project_id) if project_id else None
                project_name = proj_info.get("name") if proj_info else None
                pm_id = str(proj_info["project_manager_id"]) if proj_info and proj_info.get("project_manager_id") else None

                dev_name = users_map.get(assigned_to) if assigned_to else None

                # Consistent title: "Overdue Task — {project_name}" or "Overdue Task — Project name unavailable"
                if project_name:
                    notif_title = f"Overdue Task — {project_name}"
                else:
                    notif_title = "Overdue Task — Project name unavailable"

                # Construct clear message containing task title, developer, due date and days overdue
                dev_text = f"Assigned to {dev_name}. " if dev_name else ""
                due_text = f"Due on {due_date_str} ({days_overdue_str})." if due_date_str else f"({days_overdue_str})."
                notif_msg = f"Task: '{task_title}'. {dev_text}{due_text}"

                # Notify assigned developer
                if assigned_to:
                    NotificationService.create_notification(
                        recipient_id=assigned_to,
                        type=NotificationType.TASK_OVERDUE,
                        title=notif_title,
                        message=notif_msg,
                        project_id=project_id,
                        task_id=task_id,
                    )
                    created_count += 1

                # Notify project manager (if different from assigned dev)
                if pm_id and pm_id != assigned_to:
                    NotificationService.create_notification(
                        recipient_id=pm_id,
                        type=NotificationType.TASK_OVERDUE,
                        title=notif_title,
                        message=notif_msg,
                        project_id=project_id,
                        task_id=task_id,
                    )
                    created_count += 1

        except Exception as exc:
            # Log error silently
            print(f"Warning: Error during overdue notification scan: {exc}")

        return created_count
