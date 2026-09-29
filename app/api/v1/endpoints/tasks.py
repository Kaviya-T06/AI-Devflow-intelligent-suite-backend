"""
Tasks router — real database queries.
Returns empty list if table doesn't exist yet.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, status

from app.schemas.task import TaskCreateRequest, TaskOut, TaskPriority, TaskStatus
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _db():
    return get_supabase_client()


def _row_to_task(r: dict) -> TaskOut:
    return TaskOut(
        id=r["id"],
        title=r.get("title", ""),
        description=r.get("description"),
        status=TaskStatus(r.get("status", "todo")),
        priority=TaskPriority(r.get("priority", "medium")),
        project_id=r.get("project_id"),
        assignee_id=r.get("assignee_id"),
        due_date=r.get("due_date"),
    )


@router.get("", response_model=List[TaskOut], summary="List all tasks")
async def list_tasks() -> List[TaskOut]:
    """Retrieve all tasks from the database."""
    try:
        resp = _db().table("tasks").select("*").execute()
        return [_row_to_task(r) for r in (resp.data or [])]
    except Exception:
        return []


@router.get("/{task_id}", response_model=TaskOut, summary="Get task by ID")
async def get_task(
    task_id: str = Path(..., description="Task UUID"),
) -> TaskOut:
    """Retrieve a specific task by ID."""
    try:
        resp = _db().table("tasks").select("*").eq("id", task_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Task not found.")
        return _row_to_task(resp.data[0])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("", response_model=TaskOut, summary="Create a task", status_code=201)
async def create_task(payload: TaskCreateRequest) -> TaskOut:
    """Create a new task in the database."""
    try:
        import uuid
        resp = _db().table("tasks").insert({
            "id": str(uuid.uuid4()),
            "title": payload.title,
            "description": payload.description,
            "status": payload.status.value if payload.status else "todo",
            "priority": payload.priority.value if payload.priority else "medium",
            "project_id": payload.project_id,
            "assignee_id": payload.assignee_id,
            "due_date": payload.due_date,
        }).execute()
        if not resp.data:
            raise HTTPException(status_code=500, detail="Task creation failed.")
        return _row_to_task(resp.data[0])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
