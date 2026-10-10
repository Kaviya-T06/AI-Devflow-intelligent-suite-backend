"""
Notification Service — manages persistence, retrieval, deduplication,
and notification generation for user events.
"""
from datetime import datetime, date, timezone
from typing import Dict, List, Optional, Tuple, Any
from uuid import uuid4

from fastapi import HTTPException, status
from postgrest.exceptions import APIError

from app.db.supabase_client import get_supabase_client
from app.schemas.notification import NotificationCreate, NotificationOut, NotificationType


# In-memory fallback store for when public.notifications table is not yet migrated in Supabase
_fallback_notifications: Dict[str, Dict[str, Any]] = {}


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
                recent = [
                    v for v in _fallback_notifications.values()
                    if v["recipient_id"] == recipient_id
                    and v["type"] == type_str
                    and v["title"] == title
                    and (not task_id or v.get("task_id") == task_id)
                ]
                if recent:
                    recent.sort(key=lambda x: x["created_at"], reverse=True)
                    latest = recent[0]
                    created_dt = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
                    now_dt = datetime.now(timezone.utc)
                    if (now_dt - created_dt).total_seconds() < 60:
                        return NotificationOut(
                            id=latest["id"],
                            recipient_id=latest["recipient_id"],
                            type=NotificationType(latest["type"]),
                            title=latest["title"],
                            message=latest["message"],
                            project_id=latest.get("project_id"),
                            task_id=latest.get("task_id"),
                            is_read=latest.get("is_read", False),
                            created_at=latest["created_at"],
                        )

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
                    created_at=str(row["created_at"]),
                )
        except Exception as exc:
            if _is_table_missing_error(exc):
                # Save to in-memory fallback store
                _fallback_notifications[new_id] = payload
                return NotificationOut(
                    id=new_id,
                    recipient_id=recipient_id,
                    type=NotificationType(type_str),
                    title=title,
                    message=message,
                    project_id=project_id,
                    task_id=task_id,
                    is_read=False,
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
                return NotificationOut(
                    id=str(row["id"]),
                    recipient_id=str(row["recipient_id"]),
                    type=NotificationType(row["type"]),
                    title=row["title"],
                    message=row["message"],
                    project_id=str(row["project_id"]) if row.get("project_id") else None,
                    task_id=str(row["task_id"]) if row.get("task_id") else None,
                    is_read=bool(row.get("is_read", False)),
                    created_at=str(row["created_at"]),
                )
        except Exception as exc:
            if _is_table_missing_error(exc) and notification_id in _fallback_notifications:
                row = _fallback_notifications[notification_id]
                if row["recipient_id"] == recipient_id:
                    return NotificationOut(
                        id=row["id"],
                        recipient_id=row["recipient_id"],
                        type=NotificationType(row["type"]),
                        title=row["title"],
                        message=row["message"],
                        project_id=row.get("project_id"),
                        task_id=row.get("task_id"),
                        is_read=row.get("is_read", False),
                        created_at=row["created_at"],
                    )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or access denied.",
        )

    @staticmethod
    def list_user_notifications(
        user_id: str, limit: int = 50, offset: int = 0
    ) -> Tuple[List[NotificationOut], int, int]:
        """
        List notifications belonging strictly to user_id.
        Returns (items, unread_count, total_count).
        """
        db = get_supabase_client()
        try:
            # Query items
            resp = (
                db.table("notifications")
                .select("*")
                .eq("recipient_id", user_id)
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
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]

            # Unread count
            unread_resp = (
                db.table("notifications")
                .select("id", count="exact")
                .eq("recipient_id", user_id)
                .eq("is_read", False)
                .execute()
            )
            unread_count = unread_resp.count if unread_resp.count is not None else len([i for i in items if not i.is_read])

            total_resp = (
                db.table("notifications")
                .select("id", count="exact")
                .eq("recipient_id", user_id)
                .execute()
            )
            total_count = total_resp.count if total_resp.count is not None else len(items)

            return items, unread_count, total_count

        except Exception as exc:
            if _is_table_missing_error(exc):
                user_items = [
                    v for v in _fallback_notifications.values()
                    if v["recipient_id"] == user_id
                ]
                user_items.sort(key=lambda x: x["created_at"], reverse=True)
                paginated = user_items[offset : offset + limit]
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
                        created_at=r["created_at"],
                    )
                    for r in paginated
                ]
                unread_count = len([r for r in user_items if not r.get("is_read", False)])
                return items, unread_count, len(user_items)
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
                {"is_read": True, "updated_at": now_iso}
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
                item["updated_at"] = now_iso
                return NotificationOut(
                    id=item["id"],
                    recipient_id=item["recipient_id"],
                    type=NotificationType(item["type"]),
                    title=item["title"],
                    message=item["message"],
                    project_id=item.get("project_id"),
                    task_id=item.get("task_id"),
                    is_read=True,
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
                .update({"is_read": True, "updated_at": now_iso})
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
                        item["updated_at"] = now_iso
                        updated_count += 1
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
                return True
            raise exc

    @staticmethod
    def check_and_create_overdue_notifications() -> int:
        """
        Check for tasks with due_date < today that are not COMPLETED.
        Generate TASK_OVERDUE notifications for assignees and project managers.
        """
        db = get_supabase_client()
        today_str = date.today().isoformat()
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

            for task in overdue_tasks:
                task_id = str(task["id"])
                task_title = task.get("title", "Untitled Task")
                assigned_to = str(task["assigned_to"]) if task.get("assigned_to") else None
                project_id = str(task["project_id"]) if task.get("project_id") else None
                due_date_str = str(task.get("due_date"))

                # Fetch project manager if project exists
                pm_id = None
                if project_id:
                    proj_resp = (
                        db.table("projects")
                        .select("project_manager_id")
                        .eq("id", project_id)
                        .execute()
                    )
                    if proj_resp.data and proj_resp.data[0].get("project_manager_id"):
                        pm_id = str(proj_resp.data[0]["project_manager_id"])

                # Notify assigned developer
                if assigned_to:
                    NotificationService.create_notification(
                        recipient_id=assigned_to,
                        type=NotificationType.TASK_OVERDUE,
                        title="Task Overdue Alert",
                        message=f"Task '{task_title}' was due on {due_date_str} and is currently overdue.",
                        project_id=project_id,
                        task_id=task_id,
                    )
                    created_count += 1

                # Notify project manager (if different from assigned dev)
                if pm_id and pm_id != assigned_to:
                    NotificationService.create_notification(
                        recipient_id=pm_id,
                        type=NotificationType.TASK_OVERDUE,
                        title="Overdue Task in Project",
                        message=f"Task '{task_title}' assigned to developer was due on {due_date_str} and is overdue.",
                        project_id=project_id,
                        task_id=task_id,
                    )
                    created_count += 1

        except Exception as exc:
            # Log error silently
            print(f"Warning: Error during overdue notification scan: {exc}")

        return created_count
