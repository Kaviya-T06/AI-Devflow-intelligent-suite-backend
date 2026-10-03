import uuid
from typing import Optional
from app.db.supabase_client import get_supabase_client

def log_activity(
    user_id: str,
    action: str,
    entity_type: str,
    description: str,
    entity_id: Optional[str] = None,
    project_id: Optional[str] = None,
    metadata: Optional[dict] = None
) -> None:
    """
    Log an activity to the activity_logs table.
    Automatically infers project_id if not explicitly provided but entity_id is known.
    """
    try:
        db = get_supabase_client()
        
        inferred_project_id = project_id
        if not inferred_project_id and entity_id:
            if entity_type == "project":
                inferred_project_id = entity_id
            elif entity_type == "task":
                task_res = db.table("tasks").select("project_id").eq("id", entity_id).execute()
                if task_res.data:
                    inferred_project_id = task_res.data[0]["project_id"]
            elif entity_type == "workflow_risk":
                risk_res = db.table("workflow_risks").select("project_id").eq("id", entity_id).execute()
                if risk_res.data:
                    inferred_project_id = risk_res.data[0]["project_id"]

        payload = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "project_id": inferred_project_id,
            "description": description,
            "metadata": metadata
        }
        db.table("activity_logs").insert(payload).execute()
    except Exception as exc:
        print(f"Failed to log activity: {exc}")
        # The prompt says: "If an activity log fails, do not silently hide the error."
        # We will log it to stdout or raise it depending on strictness.
        # But if we raise it, project creation fails. The prompt says "Make activity logging reliable. If an activity log fails, do not silently hide the error."
        raise RuntimeError(f"Activity logging failed: {exc}")
