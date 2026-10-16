"""
Pydantic schemas for Project Pulse Phase 4 AI Recommendations.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class SupportingReference(BaseModel):
    type: str = Field(..., description="Reference type: task, risk, developer, workload, activity, github")
    id: str = Field(..., description="Entity ID or identifier")
    label: str = Field(..., description="Human-readable label for the entity")


class PulseRecommendation(BaseModel):
    id: str = Field(..., description="Unique recommendation ID")
    category: str = Field(
        ...,
        description="Category: RISK_MITIGATION, TASK_REVIEW, WORKLOAD_REBALANCE, ALLOCATION_OPPORTUNITY, DEADLINE_CONFIRMATION, or GENERAL"
    )
    title: str = Field(..., description="Actionable title for project managers")
    explanation: str = Field(..., description="Detailed explanation of the recommendation")
    why_it_matters: str = Field(..., description="Why this recommendation matters for project health/timeline")
    missing_info_or_assumptions: str = Field(..., description="Missing data or assumptions made")
    confidence: str = Field(..., description="Confidence label: High, Medium, or Low")
    confidence_rationale: str = Field(..., description="Documented, defensible rationale for the confidence score")
    supporting_references: List[SupportingReference] = Field(default_factory=list, description="Grounding entity references")
    actionable_entity_id: Optional[str] = Field(None, description="Optional entity ID for advisory context")
    actionable_entity_type: Optional[str] = Field(None, description="Optional entity type (task, risk, developer)")


class ProjectPulseRecommendationsResponse(BaseModel):
    project_id: str
    project_name: str
    health_score: int
    health_data_completeness: str
    recommendations: List[PulseRecommendation]
    source: str = Field(..., description="'gemini' or 'deterministic'")
    ai_status: str = Field(..., description="'SUCCESS', 'FALLBACK', or 'OFFLINE'")
    ai_notice: Optional[str] = None
    generated_at: str
