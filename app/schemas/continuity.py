from typing import List, Optional, Any, Dict
from pydantic import BaseModel
from datetime import datetime

class ContinuitySummaryOut(BaseModel):
    project_overview: str
    previous_developer_work: str
    current_work: str
    pending_work: str
    blocked_overdue_work: str
    recent_github_activity: str
    known_issues: str
    important_context: str
    what_next_developer_should_know: str
    recommended_next_steps: str
    raw_context: Optional[Dict[str, Any]] = None
    # Enhanced structured fields for Developer Handover Intelligence
    is_preliminary_inference: Optional[bool] = False
    inference_rationale: Optional[str] = None
    next_developer_plan: Optional[List[Dict[str, Any]]] = None
    attention_required: Optional[List[Dict[str, Any]]] = None
    checklist: Optional[List[Dict[str, Any]]] = None
    data_coverage: Optional[Dict[str, Any]] = None

class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str

class AIQuestionRequest(BaseModel):
    question: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None

class AIAnswerResponse(BaseModel):
    answer: str

class ProjectContextUpdateRequest(BaseModel):
    description: Optional[str] = None
    tech_stack: Optional[str] = None
    repository_url: Optional[str] = None
    handover_notes: Optional[str] = None

