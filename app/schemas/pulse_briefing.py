"""
Pydantic schemas for Phase 5: AI Project Pulse Briefing.
Defines structured models for all 9 briefing sections and verified facts.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class HealthSection(BaseModel):
    score: int = Field(..., description="Deterministic health score (0-100)")
    status: str = Field(..., description="Health status label (e.g. Healthy, Caution, At Risk)")
    data_completeness: str = Field(..., description="Completeness level: High, Medium, or Low")
    factors: List[str] = Field(default_factory=list, description="Specific factors influencing the health score")
    explanation: str = Field(..., description="Factual narrative of the health score factors")


class CompletedWorkSection(BaseModel):
    tasks_count: int = Field(..., description="Number of completed tasks")
    task_titles: List[str] = Field(default_factory=list, description="Titles of verified completed tasks")
    summary: str = Field(..., description="Narrative summary of completed work based strictly on recorded events")


class InProgressAndPendingSection(BaseModel):
    in_progress_count: int = Field(..., description="Number of in-progress tasks")
    todo_count: int = Field(..., description="Number of pending/TODO tasks")
    in_progress_tasks: List[str] = Field(default_factory=list, description="Verified in-progress task titles")
    pending_tasks: List[str] = Field(default_factory=list, description="Verified pending task titles")
    summary: str = Field(..., description="Narrative summary of active and queued tasks")


class OverdueOrBlockedSection(BaseModel):
    count: int = Field(..., description="Number of overdue or blocked tasks")
    items: List[str] = Field(default_factory=list, description="Verified overdue or blocked task details")
    summary: str = Field(..., description="Narrative summary of overdue or blocked milestones")


class UnresolvedRisksSection(BaseModel):
    critical_count: int = Field(..., description="Number of unresolved critical risks")
    high_count: int = Field(..., description="Number of unresolved high risks")
    items: List[str] = Field(default_factory=list, description="Verified unresolved critical and high risk details")
    summary: str = Field(..., description="Narrative summary of active risk exposure")


class RecentActivitySection(BaseModel):
    items: List[str] = Field(default_factory=list, description="Recent activity logs with timestamps and sources")
    summary: str = Field(..., description="Narrative synthesis of recent developer actions and timeline updates")


class GitHubContextSection(BaseModel):
    is_configured: bool = Field(..., description="Whether GitHub repository integration is connected")
    repository: Optional[str] = Field(None, description="GitHub repository full name if connected")
    recent_commits_count: int = Field(0, description="Count of recent repository commits")
    open_prs_count: int = Field(0, description="Count of open pull requests")
    branches_count: int = Field(0, description="Count of active repository branches")
    summary: str = Field(..., description="GitHub summary; missing GitHub context is never treated as project failure")


class RecommendedNextActionsSection(BaseModel):
    actions: List[str] = Field(default_factory=list, description="Prioritized next actions supported by evidence")
    summary: str = Field(..., description="Briefing recommendation summary for PM recovery focus")


class DataGapsSection(BaseModel):
    gaps: List[str] = Field(default_factory=list, description="Specific data gaps that limit briefing certainty")
    reliability_impact: str = Field(..., description="Explanation of how data gaps affect briefing confidence")


class PulseBriefingSections(BaseModel):
    health: HealthSection
    completed_work: CompletedWorkSection
    in_progress_and_pending: InProgressAndPendingSection
    overdue_or_blocked: OverdueOrBlockedSection
    unresolved_risks: UnresolvedRisksSection
    recent_activity: RecentActivitySection
    github_context: GitHubContextSection
    recommended_next_actions: RecommendedNextActionsSection
    data_gaps: DataGapsSection


class ProjectPulseBriefingResponse(BaseModel):
    project_id: str
    project_name: str
    generated_at: str = Field(..., description="ISO timestamp when briefing was generated")
    data_freshness: str = Field(..., description="Human-readable data freshness timestamp or label")
    health_score: int = Field(..., description="Deterministic health score (uncompromised)")
    health_data_completeness: str = Field(..., description="High, Medium, or Low")
    sections: PulseBriefingSections
    verified_facts: Dict[str, Any] = Field(
        default_factory=dict,
        description="Ground truth facts from database, clearly distinguished from AI narrative"
    )
    source: str = Field(..., description="'gemini' or 'deterministic'")
    ai_status: str = Field(..., description="'SUCCESS', 'FALLBACK', or 'OFFLINE'")
    ai_notice: Optional[str] = None
