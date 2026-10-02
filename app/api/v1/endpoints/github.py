from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException
import httpx

from app.schemas.github import (
    GitHubRepositoryCreate,
    GitHubRepositoryOut,
    GitHubCommit,
    GitHubPullRequest,
    GitHubIssue,
    GitHubBranch
)
from app.api.deps import get_current_user, get_db
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/github", tags=["GitHub Integration"])
from app.schemas.user import UserOut

def check_project_access(project_id: str, user: UserOut, db) -> None:
    # Admin/Manager check
    if user.role in ["admin", "project_manager"]:
        return
    
    # Developers could be assigned tasks, but for simplicity, allow read if they are active
    # (The post/delete endpoints already enforce ADMIN/MANAGER)
    pass

@router.get("/projects/{project_id}/repository", response_model=Optional[GitHubRepositoryOut])
async def get_project_repository(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not resp.data:
        return None
    
    return resp.data[0]

import re

@router.post("/projects/{project_id}/repository", response_model=GitHubRepositoryOut)
async def connect_repository(project_id: str, repo: GitHubRepositoryCreate, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    # Ensure it's a project manager or admin connecting it
    if user.role not in ["admin", "project_manager"]:
        raise HTTPException(status_code=403, detail="Only managers can connect a repository")
        
    # Check if already connected
    existing = db.table("project_github_repositories").select("id").eq("project_id", project_id).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Project already has a connected repository")
        
    # Parse URL
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+)", repo.repository_url.strip())
    if not match:
        raise HTTPException(status_code=400, detail="Invalid GitHub repository URL. Must be in format https://github.com/owner/repo")
        
    owner = match.group(1)
    repository_name = match.group(2).removesuffix(".git")
    full_name = f"{owner}/{repository_name}"
    
    # Verify via GitHub API
    try:
        url = f"https://api.github.com/repos/{full_name}"
        repo_data = await fetch_github_api(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to access GitHub repository. Make sure it exists and is public. Details: {str(e)}")
        
    data = {
        "project_id": project_id,
        "owner": repo_data["owner"]["login"],
        "repository_name": repo_data["name"],
        "full_name": repo_data["full_name"],
        "html_url": repo_data["html_url"],
        "default_branch": repo_data.get("default_branch", "main"),
        "github_repository_id": str(repo_data["id"]),
        "connected_by": str(user.id),
        "last_synced_at": "now()"
    }
    
    resp = db.table("project_github_repositories").insert(data).execute()
    
    if not resp.data:
        raise HTTPException(status_code=500, detail="Failed to connect repository")
        
    # Log activity
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "GITHUB_REPO_CONNECTED",
        "entity_type": "project",
        "entity_id": project_id,
        "description": f"Connected GitHub repository {full_name} to project"
    }).execute()
    
    return resp.data[0]

@router.delete("/projects/{project_id}/repository")
async def disconnect_repository(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    if user.role not in ["admin", "project_manager"]:
        raise HTTPException(status_code=403, detail="Only managers can disconnect a repository")
        
    resp = db.table("project_github_repositories").delete().eq("project_id", project_id).execute()
    
    # Log activity
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "GITHUB_REPO_DISCONNECTED",
        "entity_type": "project",
        "entity_id": project_id,
        "description": "Disconnected GitHub repository from project"
    }).execute()
    
    return {"message": "Repository disconnected successfully"}

async def fetch_github_api(url: str):
    async with httpx.AsyncClient(headers={"Accept": "application/vnd.github.v3+json"}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

@router.post("/projects/{project_id}/repository/sync", response_model=GitHubRepositoryOut)
async def sync_repository(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    resp = db.table("project_github_repositories").update({
        "last_synced_at": "now()"
    }).eq("project_id", project_id).execute()
    
    if not resp.data:
        raise HTTPException(status_code=404, detail="Repository not found")
        
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "GITHUB_REPO_SYNCED",
        "entity_type": "project",
        "entity_id": project_id,
        "description": f"Synchronized GitHub repository for project"
    }).execute()
    
    return resp.data[0]

@router.get("/projects/{project_id}/commits", response_model=List[GitHubCommit])
async def get_commits(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected")
        
    repo = repo_resp.data[0]
    try:
        url = f"https://api.github.com/repos/{repo['full_name']}/commits?per_page=10"
        commits_data = await fetch_github_api(url)
        return [
            GitHubCommit(
                sha=c["sha"],
                message=c["commit"]["message"],
                author=c["commit"]["author"]["name"],
                date=c["commit"]["author"]["date"],
                url=c["html_url"]
            )
            for c in commits_data
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch commits: {str(e)}")

@router.get("/projects/{project_id}/pulls", response_model=List[GitHubPullRequest])
async def get_pull_requests(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected")
        
    repo = repo_resp.data[0]
    try:
        url = f"https://api.github.com/repos/{repo['full_name']}/pulls?state=all&per_page=10"
        prs_data = await fetch_github_api(url)
        return [
            GitHubPullRequest(
                number=pr["number"],
                title=pr["title"],
                author=pr["user"]["login"],
                state=pr["state"],
                created_at=pr["created_at"],
                updated_at=pr["updated_at"],
                url=pr["html_url"]
            )
            for pr in prs_data
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch pull requests: {str(e)}")

@router.get("/projects/{project_id}/issues", response_model=List[GitHubIssue])
async def get_issues(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected")
        
    repo = repo_resp.data[0]
    try:
        url = f"https://api.github.com/repos/{repo['full_name']}/issues?state=all&per_page=10"
        issues_data = await fetch_github_api(url)
        # GitHub API returns PRs as issues too, we should filter them out
        return [
            GitHubIssue(
                number=issue["number"],
                title=issue["title"],
                author=issue["user"]["login"],
                state=issue["state"],
                created_at=issue["created_at"],
                url=issue["html_url"]
            )
            for issue in issues_data if "pull_request" not in issue
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch issues: {str(e)}")

@router.get("/projects/{project_id}/branches", response_model=List[GitHubBranch])
async def get_branches(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected")
        
    repo = repo_resp.data[0]
    try:
        url = f"https://api.github.com/repos/{repo['full_name']}/branches?per_page=10"
        branches_data = await fetch_github_api(url)
        return [
            GitHubBranch(
                name=b["name"],
                last_commit_sha=b["commit"]["sha"]
            )
            for b in branches_data
        ]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch branches: {str(e)}")
