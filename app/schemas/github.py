from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

class GitHubRepositoryBase(BaseModel):
    project_id: str
    owner: str
    repository_name: str
    full_name: str
    html_url: str
    default_branch: str = "main"

class GitHubRepositoryCreate(BaseModel):
    repository_url: str

class GitHubRepositoryOut(GitHubRepositoryBase):
    id: str
    github_repository_id: Optional[str] = None
    connected_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_synced_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class GitHubCommit(BaseModel):
    sha: str
    message: str
    author: str
    date: str
    url: str

class GitHubPullRequest(BaseModel):
    number: int
    title: str
    author: str
    state: str
    created_at: str
    updated_at: str
    url: str

class GitHubIssue(BaseModel):
    number: int
    title: str
    author: str
    state: str
    created_at: str
    url: str

class GitHubBranch(BaseModel):
    name: str
    last_commit_sha: Optional[str] = None
    last_commit_message: Optional[str] = None
    last_commit_author: Optional[str] = None
    last_commit_date: Optional[str] = None
    associated_pr: Optional[dict] = None
    linked_developer: Optional[dict] = None
    creator: str = "Creator unavailable"

class BranchDeveloperAssignRequest(BaseModel):
    developer_id: str
    task_id: Optional[str] = None

class BranchAssignableDeveloper(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    role: Optional[str] = None
    tasks: List[dict] = []

class GitHubBranchCreate(BaseModel):
    branch_name: str
    base_branch: str = "main"
    task_id: Optional[str] = None

class TaskBranchAssociate(BaseModel):
    branch_name: str

class TaskPRAssociate(BaseModel):
    pr_number: int
    pr_url: Optional[str] = None

class TaskGitHubContext(BaseModel):
    task_id: str
    project_id: str
    project_name: str
    task_title: str
    assigned_developer_id: Optional[str] = None
    assigned_developer_name: Optional[str] = None
    repository: Optional[GitHubRepositoryOut] = None
    associated_branch: Optional[str] = None
    associated_pr: Optional[dict] = None
    available_branches: list[GitHubBranch] = []
    available_pull_requests: list[GitHubPullRequest] = []

