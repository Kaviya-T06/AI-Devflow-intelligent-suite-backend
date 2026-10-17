"""
Pydantic schemas for Task Submissions and Project Manager Reviews.
"""
from datetime import date, datetime
from enum import Enum
from typing import List, Optional, Any
from pydantic import BaseModel, Field, field_validator
import re


class ReviewDecisionEnum(str, Enum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"


class SubmissionStatusEnum(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"


class TaskSubmissionCreateRequest(BaseModel):
    work_summary: str = Field(..., min_length=1, description="Summary of work completed")
    implementation_details: str = Field(..., min_length=1, description="Technical implementation details")
    testing_performed: str = Field(..., min_length=1, description="Details of testing carried out")
    pull_request_url: Optional[str] = Field(None, description="Optional GitHub pull request URL")
    deliverable_url: Optional[str] = Field(None, description="Optional deliverable or docs URL")
    additional_comments: Optional[str] = Field(None, description="Optional comments for reviewer")

    @field_validator("pull_request_url")
    @classmethod
    def validate_pr_url(cls, v: Optional[str]) -> Optional[str]:
        if not v or not v.strip():
            return None
        v = v.strip()
        if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", v):
            raise ValueError("Pull request URL must be a valid HTTP or HTTPS URL.")
        return v

    @field_validator("deliverable_url")
    @classmethod
    def validate_deliverable_url(cls, v: Optional[str]) -> Optional[str]:
        if not v or not v.strip():
            return None
        v = v.strip()
        if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", v):
            raise ValueError("Deliverable URL must be a valid HTTP or HTTPS URL.")
        return v


class TaskReviewActionRequest(BaseModel):
    decision: ReviewDecisionEnum
    review_comment: Optional[str] = Field(None, description="Feedback or reason for decision")

    @field_validator("review_comment")
    @classmethod
    def validate_comment(cls, v: Optional[str], info) -> Optional[str]:
        decision = info.data.get("decision")
        if decision in (ReviewDecisionEnum.REQUEST_CHANGES, ReviewDecisionEnum.REJECT):
            if not v or not v.strip():
                raise ValueError("A review comment is required when requesting changes or rejecting a submission.")
        return v.strip() if v else None


class TaskReviewCommentRequest(BaseModel):
    decision: Optional[ReviewDecisionEnum] = None
    review_comment: Optional[str] = None


class TaskReviewOut(BaseModel):
    id: str
    task_id: str
    submission_id: str
    reviewer_id: str
    reviewer_name: Optional[str] = None
    reviewer_email: Optional[str] = None
    decision: ReviewDecisionEnum
    review_comment: Optional[str] = None
    created_at: datetime


class TaskAttachmentOut(BaseModel):
    id: str
    task_id: str
    submission_id: str
    uploaded_by: str
    uploader_name: Optional[str] = None
    filename: str
    storage_path: str
    mime_type: str
    size: int
    created_at: datetime
    preview_url: Optional[str] = None
    download_url: Optional[str] = None


class TaskSubmissionOut(BaseModel):
    id: str
    task_id: str
    submitted_by: str
    submitter_name: Optional[str] = None
    submitter_email: Optional[str] = None
    work_summary: str
    implementation_details: str
    testing_performed: str
    pull_request_url: Optional[str] = None
    deliverable_url: Optional[str] = None
    additional_comments: Optional[str] = None
    submission_number: int = 1
    status: SubmissionStatusEnum = SubmissionStatusEnum.PENDING
    created_at: datetime
    updated_at: Optional[datetime] = None
    reviews: List[TaskReviewOut] = []
    attachments: List[TaskAttachmentOut] = []


class PendingReviewTaskOut(BaseModel):
    task_id: str
    task_title: str
    task_description: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    priority: str
    due_date: Optional[date] = None
    status: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    project_manager_id: Optional[str] = None
    project_manager_name: Optional[str] = None
    developer_id: Optional[str] = None
    developer_name: Optional[str] = None
    developer_email: Optional[str] = None
    submission_count: int = 0
    latest_submission: Optional[TaskSubmissionOut] = None
    submitted_at: Optional[datetime] = None


class GitHubEvidenceOut(BaseModel):
    available: bool = False
    message: Optional[str] = None
    repository_name: Optional[str] = None
    pull_request_url: Optional[str] = None
    pull_request_number: Optional[int] = None
    title: Optional[str] = None
    status: Optional[str] = None
    is_merged: Optional[bool] = None
    author: Optional[str] = None
    additions: Optional[int] = None
    deletions: Optional[int] = None
    changed_files_count: Optional[int] = None
    changed_files: List[dict] = []
    reviews: List[dict] = []
