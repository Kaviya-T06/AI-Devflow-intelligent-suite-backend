from typing import List, Optional
from datetime import date
from pydantic import BaseModel, Field

class SimulationBaseline(BaseModel):
    current_deadline: Optional[date] = None
    remaining_tasks: int = 0
    remaining_estimated_effort_hours: Optional[float] = None
    available_capacity_hours: Optional[float] = None

class DeadlineSimulationScenario(BaseModel):
    proposed_deadline: date
    remaining_tasks: int = 0
    remaining_estimated_effort_hours: Optional[float] = None
    available_capacity_hours: Optional[float] = None
    capacity_gap_hours: Optional[float] = None
    schedule_concerns: List[str] = []

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
    affected_tasks: List[dict] = []
    replacements: List[dict] = []
    schedule_concerns: List[str] = []

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
