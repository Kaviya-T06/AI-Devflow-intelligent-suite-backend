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
    last_commit_sha: str
