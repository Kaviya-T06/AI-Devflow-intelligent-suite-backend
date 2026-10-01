import uuid
from typing import Optional
from app.db.supabase_client import get_supabase_client

def log_activity(
    user_id: str,
    action: str,
    entity_type: str,
    description: str,
    entity_id: Optional[str] = None
) -> None:
    """
    Log an activity to the activity_logs table.
    Errors are captured but not re-raised to prevent breaking the main transaction flow,
    though in a strict audit system we might want them to fail.
    """
    try:
        db = get_supabase_client()
        payload = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "description": description
        }
        db.table("activity_logs").insert(payload).execute()
    except Exception as exc:
        print(f"Failed to log activity: {exc}")
        # The prompt says: "If an activity log fails, do not silently hide the error."
        # We will log it to stdout or raise it depending on strictness.
        # But if we raise it, project creation fails. The prompt says "Make activity logging reliable. If an activity log fails, do not silently hide the error."
        raise RuntimeError(f"Activity logging failed: {exc}")
