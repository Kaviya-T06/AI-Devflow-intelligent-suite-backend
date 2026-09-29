"""
Workflow Risks router — real database queries.
Returns empty list until workflow_risks table is created.
"""
from typing import List

from fastapi import APIRouter

from app.schemas.common import WorkflowRiskOut, RiskLevel
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/workflow-risks", tags=["Workflow Risks"])


def _db():
    return get_supabase_client()


@router.get("", response_model=List[WorkflowRiskOut], summary="Get detected workflow risks")
async def get_workflow_risks() -> List[WorkflowRiskOut]:
    """
    Retrieve all workflow risks from the database.
    Returns empty list until the workflow_risks table is created.
    """
    try:
        resp = _db().table("workflow_risks").select("*").execute()
        rows = resp.data or []
        return [
            WorkflowRiskOut(
                id=r["id"],
                title=r.get("title", ""),
                description=r.get("description", ""),
                level=RiskLevel(r.get("level", "medium")),
                project_id=r.get("project_id"),
                project_name=r.get("project_name"),
                detected_at=r.get("detected_at", r.get("created_at", "")),
                is_resolved=r.get("is_resolved", False),
            )
            for r in rows
        ]
    except Exception:
        return []
