from typing import List, Optional, Any
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
import httpx

from app.schemas.github import (
    GitHubRepositoryCreate,
    GitHubRepositoryOut,
    GitHubCommit,
    GitHubPullRequest,
    GitHubIssue,
    GitHubBranch,
    GitHubBranchCreate,
    TaskBranchAssociate,
    TaskPRAssociate,
    TaskGitHubContext,
    BranchDeveloperAssignRequest,
    BranchAssignableDeveloper,
)
from app.api.deps import get_current_user, get_db
from app.db.supabase_client import get_supabase_client

router = APIRouter(prefix="/github", tags=["GitHub Integration"])
from app.schemas.user import UserOut
from app.services.project_service import get_project_by_id_service

def check_project_access(project_id: str, user: UserOut, db) -> None:
    # Use existing DevFlow project service to enforce RBAC
    # get_project_by_id_service will raise appropriate HTTP exceptions (e.g. 403, 404)
    # if the user is unauthorized or if the project doesn't exist.
    get_project_by_id_service(project_id, user)

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
    except HTTPException:
        raise
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
        "last_synced_at": datetime.now(timezone.utc).isoformat()
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
        "project_id": project_id,
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
        "project_id": project_id,
        "description": "Disconnected GitHub repository from project"
    }).execute()
    
    return {"message": "Repository disconnected successfully"}

from app.core.config import settings

async def fetch_github_api(url: str):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AI-DevFlow-Intelligence-Suite/1.0",
    }
    if hasattr(settings, "GITHUB_TOKEN") and settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        try:
            response = await client.get(url)

            if response.status_code == 401:
                raise HTTPException(status_code=502, detail="GitHub API authentication failed.")
            elif response.status_code == 403:
                if response.headers.get("x-ratelimit-remaining") == "0":
                    raise HTTPException(status_code=502, detail="GitHub API rate limit exceeded.")
                else:
                    raise HTTPException(status_code=502, detail="GitHub API forbidden.")
            elif response.status_code == 404:
                raise HTTPException(status_code=404, detail="GitHub resource not found.")

            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Network error communicating with GitHub API: {exc}")

@router.post("/projects/{project_id}/repository/sync", response_model=GitHubRepositoryOut)
async def sync_repository(project_id: str, user: UserOut = Depends(get_current_user)):
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    resp = db.table("project_github_repositories").update({
        "last_synced_at": datetime.now(timezone.utc).isoformat()
    }).eq("project_id", project_id).execute()
    
    if not resp.data:
        raise HTTPException(status_code=404, detail="Repository not found")
        
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "GITHUB_REPO_SYNCED",
        "entity_type": "project",
        "entity_id": project_id,
        "project_id": project_id,
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
    except HTTPException:
        raise
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
    except HTTPException:
        raise
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
    except HTTPException:
        raise
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
        url = f"https://api.github.com/repos/{repo['full_name']}/branches?per_page=30"
        branches_data = await fetch_github_api(url)
        
        # Concurrently or opportunistically fetch recent commits and PRs to populate branch details
        commits_by_sha = {}
        try:
            commits_url = f"https://api.github.com/repos/{repo['full_name']}/commits?per_page=30"
            c_data = await fetch_github_api(commits_url)
            for c in c_data:
                commits_by_sha[c["sha"]] = {
                    "message": c.get("commit", {}).get("message", ""),
                    "author": c.get("commit", {}).get("author", {}).get("name", ""),
                    "date": c.get("commit", {}).get("author", {}).get("date", ""),
                }
        except Exception:
            pass

        prs_by_branch = {}
        try:
            pulls_url = f"https://api.github.com/repos/{repo['full_name']}/pulls?state=all&per_page=30"
            p_data = await fetch_github_api(pulls_url)
            for p in p_data:
                branch_ref = p.get("head", {}).get("ref")
                if branch_ref:
                    prs_by_branch[branch_ref] = {
                        "number": p["number"],
                        "title": p["title"],
                        "url": p["html_url"],
                        "state": p["state"],
                    }
        except Exception:
            pass

        # Check recorded branch creation events in activity_logs
        created_branches_map = {}
        try:
            created_logs = db.table("activity_logs").select("user_id, metadata, users(name)").eq("project_id", project_id).eq("action", "GITHUB_BRANCH_CREATED").execute()
            for log in created_logs.data:
                meta = log.get("metadata") or {}
                b_name = meta.get("branch_name")
                if b_name:
                    u_data = log.get("users") or {}
                    created_branches_map[b_name] = u_data.get("name") or "Verified Team Member"
        except Exception:
            pass

        # Check explicit branch developer assignments
        branch_dev_links = {}

        # 1. From dedicated table if available
        try:
            assigns_resp = (
                db.table("project_branch_assignments")
                .select("branch_name, developer_id, task_id, users:developer_id(id, name, email), tasks:task_id(id, title)")
                .eq("project_id", project_id)
                .execute()
            )
            for row in (assigns_resp.data or []):
                b_n = row.get("branch_name")
                u_info = row.get("users") or {}
                t_info = row.get("tasks") or {}
                if b_n and u_info:
                    branch_dev_links[b_n] = {
                        "id": str(row.get("developer_id")),
                        "name": u_info.get("name") or "Unknown Developer",
                        "email": u_info.get("email"),
                        "task_id": str(row.get("task_id")) if row.get("task_id") else None,
                        "task_title": t_info.get("title") if t_info else None,
                    }
        except Exception:
            pass

        # 2. From activity_logs (ordered oldest to newest so latest event wins)
        try:
            links_logs = (
                db.table("activity_logs")
                .select("action, metadata, created_at")
                .eq("project_id", project_id)
                .in_("action", ["BRANCH_DEVELOPER_LINKED", "BRANCH_DEVELOPER_UNLINKED"])
                .order("created_at", desc=False)
                .execute()
            )
            for log in (links_logs.data or []):
                act = log.get("action")
                meta = log.get("metadata") or {}
                b_name = meta.get("branch_name")
                if b_name:
                    if act == "BRANCH_DEVELOPER_LINKED":
                        branch_dev_links[b_name] = {
                            "id": meta.get("developer_id"),
                            "name": meta.get("developer_name") or "Unknown Developer",
                            "email": meta.get("developer_email"),
                            "task_id": meta.get("task_id"),
                            "task_title": meta.get("task_title"),
                        }
                    elif act == "BRANCH_DEVELOPER_UNLINKED":
                        branch_dev_links[b_name] = None
        except Exception as exc:
            print(f"Notice: failed querying branch links from activity_logs: {exc}")

        result: List[GitHubBranch] = []
        for b in branches_data:
            sha = b.get("commit", {}).get("sha", "")
            c_info = commits_by_sha.get(sha, {})
            associated_pr = prs_by_branch.get(b["name"])
            
            # Creator attribution: only if verified recorded creation event exists
            creator_name = created_branches_map.get(b["name"], "Creator unavailable")
            
            # Explicit linked developer from assignment
            linked_dev = branch_dev_links.get(b["name"])

            # Commit author name (or Unknown if not present)
            author_name = c_info.get("author") or "Unknown"

            result.append(
                GitHubBranch(
                    name=b["name"],
                    last_commit_sha=sha,
                    last_commit_message=c_info.get("message"),
                    last_commit_author=author_name,
                    last_commit_date=c_info.get("date"),
                    associated_pr=associated_pr,
                    linked_developer=linked_dev,
                    creator=creator_name
                )
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch branches: {str(e)}")


@router.get("/projects/{project_id}/assignable-developers", response_model=List[BranchAssignableDeveloper])
async def get_project_assignable_developers(
    project_id: str,
    user: UserOut = Depends(get_current_user),
):
    """
    Returns developers belonging to the selected project, along with their tasks in this project,
    for the Project Manager to link to discovered branches.
    """
    db = get_supabase_client()
    check_project_access(project_id, user, db)

    # Fetch project tasks to identify assigned developers and their tasks
    tasks_resp = db.table("tasks").select("id, title, status, assigned_to").eq("project_id", project_id).execute()
    tasks_data = tasks_resp.data or []

    assigned_dev_ids = set(str(t["assigned_to"]) for t in tasks_data if t.get("assigned_to"))

    # Fetch active developers and team members
    users_resp = db.table("users").select("id, name, email, role, is_active").execute()
    all_users = users_resp.data or []

    result = []
    for u in all_users:
        if not u.get("is_active", True):
            continue
        role_str = str(u.get("role", "")).lower()
        uid = str(u["id"])
        if uid in assigned_dev_ids or role_str in ["developer", "team_member"]:
            dev_tasks = [
                {"id": str(t["id"]), "title": t.get("title", ""), "status": t.get("status", "")}
                for t in tasks_data if str(t.get("assigned_to")) == uid
            ]
            result.append(
                BranchAssignableDeveloper(
                    id=uid,
                    name=u.get("name") or "Unknown Developer",
                    email=u.get("email"),
                    role=u.get("role"),
                    tasks=dev_tasks,
                )
            )
    return result


@router.post("/projects/{project_id}/branches/{branch_name:path}/assign")
async def assign_branch_developer(
    project_id: str,
    branch_name: str,
    payload: BranchDeveloperAssignRequest,
    user: UserOut = Depends(get_current_user),
):
    """
    Link a developer to a discovered GitHub branch, and optionally map an assigned task.
    """
    from urllib.parse import unquote
    branch_name = unquote(branch_name).strip()
    db = get_supabase_client()
    check_project_access(project_id, user, db)

    if user.role not in ["admin", "project_manager"]:
        raise HTTPException(status_code=403, detail="Only project managers or administrators can link developers to branches")

    # Verify repository connected
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected to this project")
    repo = repo_resp.data[0]

    # Validate developer exists and is active
    dev_resp = db.table("users").select("id, name, email, role, is_active").eq("id", payload.developer_id).execute()
    if not dev_resp.data:
        raise HTTPException(status_code=404, detail="Selected developer not found")
    dev = dev_resp.data[0]

    # Validate task if provided
    task_info = None
    if payload.task_id:
        task_resp = db.table("tasks").select("id, title, project_id, assigned_to").eq("id", payload.task_id).execute()
        if not task_resp.data:
            raise HTTPException(status_code=404, detail="Selected task not found")
        task_info = task_resp.data[0]
        if str(task_info.get("project_id")) != project_id:
            raise HTTPException(status_code=400, detail="Selected task does not belong to this project")
        if str(task_info.get("assigned_to")) != str(dev["id"]):
            raise HTTPException(status_code=400, detail="Selected task is not assigned to this developer")

    # Check existing assignment to detect changes and avoid duplicates
    prev_dev_id = None
    prev_task_id = None
    try:
        cur_assign = db.table("project_branch_assignments").select("developer_id, task_id").eq("project_id", project_id).eq("branch_name", branch_name).execute()
        if cur_assign.data:
            prev_dev_id = str(cur_assign.data[0].get("developer_id"))
            prev_task_id = cur_assign.data[0].get("task_id")
    except Exception:
        pass

    if not prev_dev_id:
        try:
            logs = db.table("activity_logs").select("action, metadata").eq("project_id", project_id).in_("action", ["BRANCH_DEVELOPER_LINKED", "BRANCH_DEVELOPER_UNLINKED"]).order("created_at", desc=True).limit(25).execute()
            for l in (logs.data or []):
                m = l.get("metadata") or {}
                if m.get("branch_name") == branch_name:
                    if l.get("action") == "BRANCH_DEVELOPER_LINKED":
                        prev_dev_id = str(m.get("developer_id")) if m.get("developer_id") else None
                        prev_task_id = m.get("task_id")
                    break
        except Exception:
            pass

    is_unchanged = (prev_dev_id == str(dev["id"])) and (prev_task_id == payload.task_id)

    meta = {
        "branch_name": branch_name,
        "developer_id": str(dev["id"]),
        "developer_name": dev.get("name"),
        "developer_email": dev.get("email"),
        "task_id": payload.task_id,
        "task_title": task_info.get("title") if task_info else None,
        "assigned_by": str(user.id),
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "repository": repo.get("full_name")
    }

    # Try storing in dedicated table if it exists
    try:
        db.table("project_branch_assignments").upsert({
            "project_id": project_id,
            "branch_name": branch_name,
            "developer_id": str(dev["id"]),
            "task_id": payload.task_id,
            "assigned_by": str(user.id),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="project_id,branch_name").execute()
    except Exception:
        pass

    # Record in activity_logs
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "BRANCH_DEVELOPER_LINKED",
        "entity_type": "branch",
        "entity_id": project_id,
        "project_id": project_id,
        "description": f"Linked developer '{dev.get('name')}' to branch '{branch_name}'",
        "metadata": meta
    }).execute()

    # If task associated, also record TASK_BRANCH_LINKED for the task
    if payload.task_id and task_info:
        try:
            db.table("activity_logs").insert({
                "user_id": str(user.id),
                "action": "TASK_BRANCH_LINKED",
                "entity_type": "task",
                "entity_id": payload.task_id,
                "project_id": project_id,
                "description": f"Branch '{branch_name}' mapped to task '{task_info['title']}'",
                "metadata": {
                    "branch_name": branch_name,
                    "repository": repo.get("full_name"),
                    "repository_id": repo.get("id")
                }
            }).execute()
        except Exception:
            pass

    # Send in-app notification if this assignment is new or changed (prevent duplicate notifications)
    if not is_unchanged:
        try:
            from app.services.notification_service import NotificationService
            from app.schemas.notification import NotificationType

            project_resp = db.table("projects").select("id, name").eq("id", project_id).execute()
            project_name = project_resp.data[0].get("name", "Project") if project_resp.data else "Project"
            pm_name = getattr(user, "name", None) or getattr(user, "full_name", None) or user.email

            task_suffix = f" Associated task: '{task_info['title']}'." if task_info else ""
            pm_suffix = f" Assigned by: {pm_name}." if pm_name else ""
            notif_msg = f"You have been linked to the branch '{branch_name}' in project '{project_name}'.{task_suffix}{pm_suffix}"

            NotificationService.create_notification(
                recipient_id=str(dev["id"]),
                type=NotificationType.GITHUB_EVENT,
                title="GitHub Branch Assigned",
                message=notif_msg,
                project_id=project_id,
                task_id=payload.task_id,
            )
        except Exception as notif_err:
            print(f"Notice: Failed to create branch assignment notification: {notif_err}")

    return {
        "message": f"Branch '{branch_name}' successfully linked to {dev.get('name')}",
        "branch_name": branch_name,
        "developer_id": str(dev["id"]),
        "developer_name": dev.get("name"),
        "task_id": payload.task_id,
        "task_title": task_info.get("title") if task_info else None
    }


@router.delete("/projects/{project_id}/branches/{branch_name:path}/assign")
async def unassign_branch_developer(
    project_id: str,
    branch_name: str,
    user: UserOut = Depends(get_current_user),
):
    """
    Remove the linked developer association from a GitHub branch.
    """
    from urllib.parse import unquote
    branch_name = unquote(branch_name).strip()
    db = get_supabase_client()
    check_project_access(project_id, user, db)

    if user.role not in ["admin", "project_manager"]:
        raise HTTPException(status_code=403, detail="Only project managers or administrators can remove branch links")

    # Find previous assignment to notify unlinked developer if needed
    prev_dev_id = None
    try:
        cur_assign = db.table("project_branch_assignments").select("developer_id").eq("project_id", project_id).eq("branch_name", branch_name).execute()
        if cur_assign.data:
            prev_dev_id = str(cur_assign.data[0].get("developer_id"))
    except Exception:
        pass

    if not prev_dev_id:
        try:
            logs = db.table("activity_logs").select("action, metadata").eq("project_id", project_id).in_("action", ["BRANCH_DEVELOPER_LINKED", "BRANCH_DEVELOPER_UNLINKED"]).order("created_at", desc=True).limit(25).execute()
            for l in (logs.data or []):
                m = l.get("metadata") or {}
                if m.get("branch_name") == branch_name:
                    if l.get("action") == "BRANCH_DEVELOPER_LINKED":
                        prev_dev_id = str(m.get("developer_id")) if m.get("developer_id") else None
                    break
        except Exception:
            pass

    # Try removing from dedicated table
    try:
        db.table("project_branch_assignments").delete().eq("project_id", project_id).eq("branch_name", branch_name).execute()
    except Exception:
        pass

    # Record in activity_logs
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "BRANCH_DEVELOPER_UNLINKED",
        "entity_type": "branch",
        "entity_id": project_id,
        "project_id": project_id,
        "description": f"Unlinked developer from branch '{branch_name}'",
        "metadata": {
            "branch_name": branch_name,
            "unlinked_by": str(user.id),
            "unlinked_at": datetime.now(timezone.utc).isoformat()
        }
    }).execute()

    # Optionally notify the previous developer that they have been unlinked
    if prev_dev_id:
        try:
            project_resp = db.table("projects").select("id, name").eq("id", project_id).execute()
            project_name = project_resp.data[0].get("name", "Project") if project_resp.data else "Project"
            from app.services.notification_service import NotificationService
            from app.schemas.notification import NotificationType
            NotificationService.create_notification(
                recipient_id=prev_dev_id,
                type=NotificationType.GITHUB_EVENT,
                title="GitHub Branch Unlinked",
                message=f"You have been unlinked from branch '{branch_name}' in project '{project_name}'.",
                project_id=project_id,
            )
        except Exception as notif_err:
            print(f"Notice: Failed to create branch unassign notification: {notif_err}")

    return {"message": f"Branch '{branch_name}' unlinked successfully", "branch_name": branch_name}


@router.post("/projects/{project_id}/branches", response_model=GitHubBranch)
async def create_branch(
    project_id: str,
    payload: GitHubBranchCreate,
    user: UserOut = Depends(get_current_user)
):
    """
    Creates a new branch on the connected GitHub repository (Section 9).
    Validates permissions, verifies base branch, ensures branch name is valid,
    creates branch via GitHub API, and logs the activity.
    """
    db = get_supabase_client()
    check_project_access(project_id, user, db)
    
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected to this project")
    repo = repo_resp.data[0]
    
    # Permission check: PM/Admin, or Developer assigned to task in project
    if user.role not in ["admin", "project_manager"]:
        if payload.task_id:
            task_resp = db.table("tasks").select("*").eq("id", payload.task_id).eq("project_id", project_id).execute()
            if not task_resp.data or task_resp.data[0].get("assigned_to") != str(user.id):
                raise HTTPException(status_code=403, detail="Developers can only create branches for their assigned tasks")
        else:
            raise HTTPException(status_code=403, detail="Only managers can create unassociated repository branches")

    branch_name = payload.branch_name.strip()
    if not re.match(r"^[a-zA-Z0-9_\-\/]+$", branch_name):
        raise HTTPException(status_code=400, detail="Invalid branch name format. Use letters, numbers, hyphens, underscores, or slashes.")
        
    # Verify base branch exists and obtain base commit SHA
    try:
        base_branch_data = await fetch_github_api(f"https://api.github.com/repos/{repo['full_name']}/branches/{payload.base_branch}")
        base_sha = base_branch_data["commit"]["sha"]
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(status_code=400, detail=f"Base branch '{payload.base_branch}' does not exist on repository")
        raise
        
    # Check whether branch already exists
    try:
        existing = await fetch_github_api(f"https://api.github.com/repos/{repo['full_name']}/branches/{branch_name}")
        if existing:
            raise HTTPException(status_code=400, detail=f"Branch '{branch_name}' already exists on repository")
    except HTTPException as e:
        if e.status_code != 404:
            raise
            
    # Create branch using GitHub API POST /repos/{owner}/{repo}/git/refs
    headers = {"Accept": "application/vnd.github.v3+json"}
    if hasattr(settings, "GITHUB_TOKEN") and settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
        
    async with httpx.AsyncClient(headers=headers) as client:
        ref_payload = {"ref": f"refs/heads/{branch_name}", "sha": base_sha}
        create_res = await client.post(f"https://api.github.com/repos/{repo['full_name']}/git/refs", json=ref_payload)
        if create_res.status_code in [401, 403]:
            raise HTTPException(status_code=502, detail="GitHub API permission denied: repository access token requires 'repo' or 'contents:write' permission to create branches.")
        elif create_res.status_code not in [200, 201]:
            raise HTTPException(status_code=create_res.status_code, detail=f"Failed to create branch: {create_res.text}")
            
    # Persist activity log with full metadata
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "GITHUB_BRANCH_CREATED",
        "entity_type": "project",
        "entity_id": project_id,
        "project_id": project_id,
        "description": f"Created branch '{branch_name}' from '{payload.base_branch}' by {user.name}",
        "metadata": {
            "branch_name": branch_name,
            "base_branch": payload.base_branch,
            "sha": base_sha,
            "task_id": payload.task_id
        }
    }).execute()
    
    # If task_id was supplied, associate branch with task immediately
    if payload.task_id:
        db.table("activity_logs").insert({
            "user_id": str(user.id),
            "action": "TASK_BRANCH_LINKED",
            "entity_type": "task",
            "entity_id": payload.task_id,
            "project_id": project_id,
            "description": f"Branch '{branch_name}' mapped to task by {user.name}",
            "metadata": {
                "branch_name": branch_name,
                "repository": repo["full_name"],
                "created_branch": True
            }
        }).execute()
        
    return GitHubBranch(
        name=branch_name,
        last_commit_sha=base_sha,
        creator=user.name,
        last_commit_message=f"Created from {payload.base_branch}"
    )


@router.post("/tasks/{task_id}/branch")
async def associate_task_branch(
    task_id: str,
    payload: TaskBranchAssociate,
    user: UserOut = Depends(get_current_user)
):
    """
    Associates an actual GitHub repository branch with an authorized coding task (Section 8).
    """
    db = get_supabase_client()
    task_resp = db.table("tasks").select("*").eq("id", task_id).execute()
    if not task_resp.data:
        raise HTTPException(status_code=404, detail="Task not found")
    task = task_resp.data[0]
    project_id = task.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Task is not assigned to a project")
        
    check_project_access(project_id, user, db)
    
    if user.role not in ["admin", "project_manager"]:
        if task.get("assigned_to") != str(user.id):
            raise HTTPException(status_code=403, detail="Only assigned developer or project manager can associate branches")
            
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected to this project")
    repo = repo_resp.data[0]
    
    # Verify branch exists on GitHub
    try:
        await fetch_github_api(f"https://api.github.com/repos/{repo['full_name']}/branches/{payload.branch_name}")
    except HTTPException:
        raise HTTPException(status_code=400, detail=f"Branch '{payload.branch_name}' does not exist on connected repository {repo['full_name']}")
        
    # Persist in activity_logs
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "TASK_BRANCH_LINKED",
        "entity_type": "task",
        "entity_id": task_id,
        "project_id": project_id,
        "description": f"Branch '{payload.branch_name}' mapped to task '{task['title']}' by {user.name}",
        "metadata": {
            "branch_name": payload.branch_name,
            "repository": repo["full_name"],
            "repository_id": repo["id"]
        }
    }).execute()
    
    return {"message": "Branch associated successfully", "branch_name": payload.branch_name, "task_id": task_id}


@router.post("/tasks/{task_id}/pull-request")
async def associate_task_pull_request(
    task_id: str,
    payload: TaskPRAssociate,
    user: UserOut = Depends(get_current_user)
):
    """
    Associates an actual GitHub pull request with an assigned coding task (Section 10).
    """
    db = get_supabase_client()
    task_resp = db.table("tasks").select("*").eq("id", task_id).execute()
    if not task_resp.data:
        raise HTTPException(status_code=404, detail="Task not found")
    task = task_resp.data[0]
    project_id = task.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Task is not assigned to a project")
        
    check_project_access(project_id, user, db)
    
    if user.role not in ["admin", "project_manager"]:
        if task.get("assigned_to") != str(user.id):
            raise HTTPException(status_code=403, detail="Only assigned developer or project manager can associate pull requests")
            
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    if not repo_resp.data:
        raise HTTPException(status_code=404, detail="No repository connected to this project")
    repo = repo_resp.data[0]
    
    # Verify PR exists on connected repository
    try:
        pr_data = await fetch_github_api(f"https://api.github.com/repos/{repo['full_name']}/pulls/{payload.pr_number}")
    except HTTPException:
        raise HTTPException(status_code=400, detail=f"Pull request #{payload.pr_number} does not exist on connected repository {repo['full_name']}")
        
    db.table("activity_logs").insert({
        "user_id": str(user.id),
        "action": "TASK_PR_LINKED",
        "entity_type": "task",
        "entity_id": task_id,
        "project_id": project_id,
        "description": f"Pull Request #{payload.pr_number} ('{pr_data.get('title')}') linked to task '{task['title']}' by {user.name}",
        "metadata": {
            "pr_number": payload.pr_number,
            "pr_title": pr_data.get("title"),
            "pr_url": pr_data.get("html_url"),
            "pr_state": pr_data.get("state"),
            "source_branch": pr_data.get("head", {}).get("ref"),
            "target_branch": pr_data.get("base", {}).get("ref"),
            "author": pr_data.get("user", {}).get("login"),
            "repository": repo["full_name"]
        }
    }).execute()
    
    return {
        "message": "Pull request associated successfully",
        "pr_number": payload.pr_number,
        "pr_title": pr_data.get("title"),
        "pr_url": pr_data.get("html_url"),
        "task_id": task_id
    }


@router.get("/tasks/{task_id}/github-context", response_model=TaskGitHubContext)
async def get_task_github_context(task_id: str, user: UserOut = Depends(get_current_user)):
    """
    Returns verified GitHub context for a task: mapped branch, mapped PR, connected repo, and available branches (Section 8).
    """
    db = get_supabase_client()
    task_resp = db.table("tasks").select("*, projects(id, name), users:assigned_to(id, name)").eq("id", task_id).execute()
    if not task_resp.data:
        raise HTTPException(status_code=404, detail="Task not found")
    task = task_resp.data[0]
    project_id = task.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Task has no project assigned")
        
    check_project_access(project_id, user, db)
    
    repo_resp = db.table("project_github_repositories").select("*").eq("project_id", project_id).execute()
    repo = repo_resp.data[0] if repo_resp.data else None
    
    logs_resp = db.table("activity_logs").select("*").eq("entity_id", task_id).in_("action", ["TASK_BRANCH_LINKED", "TASK_PR_LINKED"]).order("created_at", desc=True).execute()
    
    associated_branch = None
    associated_pr = None
    for log in logs_resp.data:
        if log["action"] == "TASK_BRANCH_LINKED" and not associated_branch:
            associated_branch = (log.get("metadata") or {}).get("branch_name")
        if log["action"] == "TASK_PR_LINKED" and not associated_pr:
            associated_pr = log.get("metadata")
            
    available_branches = []
    available_prs = []
    if repo:
        try:
            b_list = await fetch_github_api(f"https://api.github.com/repos/{repo['full_name']}/branches?per_page=30")
            available_branches = [GitHubBranch(name=b["name"], last_commit_sha=b["commit"]["sha"]) for b in b_list]
        except Exception:
            pass
        try:
            p_list = await fetch_github_api(f"https://api.github.com/repos/{repo['full_name']}/pulls?state=all&per_page=30")
            available_prs = [
                GitHubPullRequest(
                    number=p["number"],
                    title=p["title"],
                    author=p["user"]["login"],
                    state=p["state"],
                    created_at=p["created_at"],
                    updated_at=p["updated_at"],
                    url=p["html_url"]
                ) for p in p_list
            ]
        except Exception:
            pass
            
    proj_info = task.get("projects") or {}
    dev_info = task.get("users") or {}
    
    return TaskGitHubContext(
        task_id=task_id,
        project_id=project_id,
        project_name=proj_info.get("name") or "Project",
        task_title=task.get("title", ""),
        assigned_developer_id=task.get("assigned_to"),
        assigned_developer_name=dev_info.get("name"),
        repository=repo,
        associated_branch=associated_branch,
        associated_pr=associated_pr,
        available_branches=available_branches,
        available_pull_requests=available_prs
    )

