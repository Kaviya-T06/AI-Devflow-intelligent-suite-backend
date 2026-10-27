"""
Repositories router — real database queries against project_github_repositories.
"""
from typing import List

from fastapi import APIRouter

from app.schemas.common import RepositoryOut
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/repositories", tags=["Repositories"])


def _db():
    return get_supabase_client()


@router.get("", response_model=List[RepositoryOut], summary="List connected repositories")
async def list_repositories() -> List[RepositoryOut]:
    """
    Retrieve all connected GitHub repositories from the project_github_repositories table.
    Maps DB columns to the frontend Repository type: repository_name, repository_url,
    provider (always 'GitHub'), status (always 'Active'), connected_at.
    """
    try:
        resp = _db().table("project_github_repositories").select("*").execute()
        rows = resp.data or []
        return [
            RepositoryOut(
                id=r["id"],
                project_id=r.get("project_id"),
                repository_name=r.get("repository_name", ""),
                repository_url=r.get("html_url", ""),
                provider="GitHub",
                status="Active",
                connected_at=r.get("created_at"),
            )
            for r in rows
        ]
    except Exception:
        return []
