"""
Projects router — real database queries.
Tables: projects (not yet migrated — returns empty list until DB table exists)
"""
from typing import List

from fastapi import APIRouter, HTTPException, Path, status

from app.schemas.project import ProjectCreateRequest, ProjectOut, ProjectStatus
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/projects", tags=["Projects"])


def _db():
    return get_supabase_client()


@router.get("", response_model=List[ProjectOut], summary="List all projects")
async def list_projects() -> List[ProjectOut]:
    """Retrieve all projects from the database."""
    try:
        resp = _db().table("projects").select("*").execute()
        rows = resp.data or []
        return [
            ProjectOut(
                id=r["id"],
                name=r.get("name", ""),
                description=r.get("description"),
                status=ProjectStatus(r.get("status", "planning")),
                start_date=r.get("start_date"),
                end_date=r.get("end_date"),
                owner_id=r.get("owner_id"),
                member_count=r.get("member_count", 0),
                task_count=r.get("task_count", 0),
            )
            for r in rows
        ]
    except Exception:
        # Table doesn't exist yet — return empty list (no dummy data)
        return []


@router.get("/{project_id}", response_model=ProjectOut, summary="Get project by ID")
async def get_project(
    project_id: str = Path(..., description="Project UUID"),
) -> ProjectOut:
    """Retrieve a specific project by ID."""
    try:
        resp = _db().table("projects").select("*").eq("id", project_id).limit(1).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Project not found.")
        r = resp.data[0]
        return ProjectOut(
            id=r["id"],
            name=r.get("name", ""),
            description=r.get("description"),
            status=ProjectStatus(r.get("status", "planning")),
            start_date=r.get("start_date"),
            end_date=r.get("end_date"),
            owner_id=r.get("owner_id"),
            member_count=r.get("member_count", 0),
            task_count=r.get("task_count", 0),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("", response_model=ProjectOut, summary="Create a project", status_code=201)
async def create_project(payload: ProjectCreateRequest) -> ProjectOut:
    """Create a new project in the database."""
    try:
        import uuid
        project_id = str(uuid.uuid4())
        resp = _db().table("projects").insert({
            "id": project_id,
            "name": payload.name,
            "description": payload.description,
            "status": payload.status.value if payload.status else "planning",
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "owner_id": payload.owner_id,
        }).execute()
        if not resp.data:
            raise HTTPException(status_code=500, detail="Project creation failed.")
        r = resp.data[0]
        return ProjectOut(
            id=r["id"],
            name=r.get("name", ""),
            description=r.get("description"),
            status=ProjectStatus(r.get("status", "planning")),
            start_date=r.get("start_date"),
            end_date=r.get("end_date"),
            owner_id=r.get("owner_id"),
            member_count=0,
            task_count=0,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
