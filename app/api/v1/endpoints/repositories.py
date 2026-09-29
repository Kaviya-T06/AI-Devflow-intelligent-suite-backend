"""
Repositories router — real database queries.
Returns empty list until repositories table is created.
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
    Retrieve all connected repositories from the database.
    Returns empty list until the repositories table is created.
    """
    try:
        resp = _db().table("repositories").select("*").execute()
        rows = resp.data or []
        return [
            RepositoryOut(
                id=r["id"],
                name=r.get("name", ""),
                full_name=r.get("full_name", ""),
                description=r.get("description"),
                url=r.get("url", ""),
                language=r.get("language"),
                stars=r.get("stars", 0),
                open_issues=r.get("open_issues", 0),
                last_pushed_at=r.get("last_pushed_at"),
                project_id=r.get("project_id"),
            )
            for r in rows
        ]
    except Exception:
        return []
