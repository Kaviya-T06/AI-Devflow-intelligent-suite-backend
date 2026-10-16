from typing import List, Optional, Dict, Any
from datetime import date
from pydantic import BaseModel, Field

class DeveloperCapacityInfo(BaseModel):
    developer_id: str
    name: str
    email: Optional[str] = None
    capacity_hours_per_week: Optional[float] = None
    has_capacity: bool = False
    assigned_tasks_count: int = 0

class TaskEstimateUpdateRequest(BaseModel):
    estimated_hours: float = Field(..., ge=0, description="Estimated effort in hours")

class DeveloperCapacityUpdateRequest(BaseModel):
    capacity_hours_per_week: float = Field(..., ge=0, le=168, description="Weekly capacity in hours")

class ProjectSimulatorData(BaseModel):
    project_id: str
    project_name: str
    current_deadline: Optional[date] = None
    total_remaining_tasks: int = 0
    tasks_with_estimates: int = 0
    tasks_missing_estimates: int = 0
    total_estimated_effort_hours: float = 0.0
    developers_with_capacity: int = 0
    developers_missing_capacity: int = 0
    sufficient_data_for_simulation: bool = False
    tasks: List[Dict[str, Any]] = []
    developers: List[DeveloperCapacityInfo] = []

class SimulationBaseline(BaseModel):
    current_deadline: Optional[date] = None
    remaining_tasks: int = 0
    remaining_estimated_effort_hours: Optional[float] = None
    available_capacity_hours: Optional[float] = None
    working_days: int = 0
    working_weeks: float = 0.0
    tasks_with_estimates: int = 0
    tasks_missing_estimates: int = 0
    developers_with_capacity: int = 0
    developers_missing_capacity: int = 0

class DeadlineSimulationScenario(BaseModel):
    proposed_deadline: date
    remaining_tasks: int = 0
    remaining_estimated_effort_hours: Optional[float] = None
    available_capacity_hours: Optional[float] = None
    capacity_gap_hours: Optional[float] = None
    capacity_gap_status: str = "calculated"  # "calculated" | "partial" | "unavailable"
    working_days: int = 0
    working_weeks: float = 0.0
    tasks_with_estimates: int = 0
    tasks_missing_estimates: int = 0
    developers_with_capacity: int = 0
    developers_missing_capacity: int = 0
    schedule_concerns: List[str] = []
    missing_data_items: List[str] = []

class DeveloperUnavailabilitySimulationRequest(BaseModel):
    developer_id: str = Field(..., description="The ID of the unavailable developer")
    start_date: date = Field(..., description="Start date of unavailability")
    end_date: date = Field(..., description="End date of unavailability")

class UnavailabilitySimulationScenario(BaseModel):
    developer_id: str
    start_date: date
    end_date: date
    capacity_removed_hours: float = 0
    remaining_tasks: int = 0
    remaining_estimated_effort_hours: Optional[float] = None
    available_capacity_hours: Optional[float] = None
    capacity_gap_hours: Optional[float] = None
    capacity_gap_status: str = "calculated"
    working_days: int = 0
    working_weeks: float = 0.0
    tasks_with_estimates: int = 0
    tasks_missing_estimates: int = 0
    developers_with_capacity: int = 0
    developers_missing_capacity: int = 0
    affected_tasks: List[dict] = []
    replacements: List[dict] = []
    schedule_concerns: List[str] = []
    missing_data_items: List[str] = []

class DeadlineSimulationRequest(BaseModel):
    proposed_deadline: date = Field(..., description="The hypothetical new project deadline")

class DeadlineSimulationResponse(BaseModel):
    baseline: SimulationBaseline
    scenario: DeadlineSimulationScenario
    assumptions_and_limitations: List[str] = []
    is_simulation: bool = True
    ai_explanation: Optional[str] = None

class UnavailabilitySimulationResponse(BaseModel):
    baseline: SimulationBaseline
    scenario: UnavailabilitySimulationScenario
    assumptions_and_limitations: List[str] = []
    is_simulation: bool = True
    ai_explanation: Optional[str] = None

