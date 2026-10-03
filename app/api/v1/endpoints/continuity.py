from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_current_user
from app.schemas.user import UserOut
from app.schemas.continuity import ContinuitySummaryOut, AIQuestionRequest, AIAnswerResponse
from app.services.continuity_service import generate_continuity_summary_service, ask_continuity_question_service
from app.services.project_service import get_project_by_id_service

router = APIRouter(prefix="/projects/{project_id}/continuity", tags=["AI Continuity"])

def check_project_access(project_id: str, user: UserOut):
    # Reuses the access checks inside get_project_by_id_service
    # If the user is not allowed to access this project, this service call will throw a 403 HTTP Exception.
    get_project_by_id_service(project_id, user)

@router.post("/generate", response_model=ContinuitySummaryOut)
async def generate_continuity_summary(project_id: str, current_user: UserOut = Depends(get_current_user)):
    """
    Generates a new AI Continuity Summary using verified project context.
    Access restricted to Project Managers and Developers assigned to the project.
    """
    check_project_access(project_id, current_user)
    return await generate_continuity_summary_service(project_id, current_user)

@router.post("/ask", response_model=AIAnswerResponse)
async def ask_continuity_question(project_id: str, request: AIQuestionRequest, current_user: UserOut = Depends(get_current_user)):
    """
    Asks the AI a specific question about the project based on verified context.
    """
    check_project_access(project_id, current_user)
    answer = await ask_continuity_question_service(project_id, request.question, current_user)
    return AIAnswerResponse(answer=answer)
