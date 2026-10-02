from pydantic import BaseModel
from typing import List, Optional

class ProjectMetrics(BaseModel):
    total_tasks: int = 0
    completed_tasks: int = 0
    pending_tasks: int = 0
    in_progress_tasks: int = 0
    review_tasks: int = 0
    overdue_tasks: int = 0
    completion_percentage: float = 0.0
    average_task_completion_time_hours: float = 0.0
    average_task_cycle_time_hours: float = 0.0

class TaskPerformanceMetrics(BaseModel):
    total_tasks_created: int = 0
    total_completed: int = 0
    total_overdue: int = 0
    completion_rate: float = 0.0
    average_completion_time_hours: float = 0.0
    average_time_in_progress_hours: float = 0.0
    average_review_duration_hours: float = 0.0

class DeveloperMetrics(BaseModel):
    developer_id: str
    developer_name: str
    tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_in_progress: int = 0
    tasks_waiting_for_review: int = 0
    overdue_tasks: int = 0
    completion_rate: float = 0.0

class TeamWorkflowMetrics(BaseModel):
    developers: List[DeveloperMetrics] = []

class ProjectHealth(BaseModel):
    project_progress: float = 0.0
    completion_rate: float = 0.0
    overdue_task_count: int = 0
    tasks_waiting_for_review: int = 0
    active_tasks: int = 0
    remaining_tasks: int = 0

class TrendDataPoint(BaseModel):
    date: str
    value: float

class WorkflowTrends(BaseModel):
    tasks_completed_over_time: List[TrendDataPoint] = []
    tasks_created_over_time: List[TrendDataPoint] = []
    overdue_tasks_over_time: List[TrendDataPoint] = []
    project_progress_over_time: List[TrendDataPoint] = []
    average_completion_time_over_time: List[TrendDataPoint] = []

class AnalyticsDashboardData(BaseModel):
    project_metrics: ProjectMetrics
    task_performance_metrics: TaskPerformanceMetrics
    team_workflow_metrics: Optional[TeamWorkflowMetrics] = None
    project_health: ProjectHealth
    workflow_trends: WorkflowTrends
