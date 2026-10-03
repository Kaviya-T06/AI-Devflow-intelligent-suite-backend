from typing import List, Optional, Any
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

class AIQuestionRequest(BaseModel):
    question: str

class AIAnswerResponse(BaseModel):
    answer: str
