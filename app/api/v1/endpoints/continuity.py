import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.api.deps import get_current_user
from app.schemas.user import UserOut
from app.schemas.continuity import ContinuitySummaryOut, AIQuestionRequest, AIAnswerResponse, ProjectContextUpdateRequest
from app.services.continuity_service import (
    generate_continuity_summary_service, 
    ask_continuity_question_service,
    stream_continuity_question_service,
    get_grounded_data_briefing_service,
    update_project_context_and_refresh_handover,
)
from app.services.project_service import get_project_by_id_service
from app.services.ollama_service import check_ollama_health, warmup_ollama
from app.core.config import settings

router = APIRouter(prefix="/projects/{project_id}/continuity", tags=["AI Continuity"])
general_router = APIRouter(prefix="/continuity", tags=["AI Continuity General"])

def check_project_access(project_id: str, user: UserOut):
    if not project_id or project_id in ("general", "default", "none"):
        return
    # Reuses the access checks inside get_project_by_id_service
    # If the user is not allowed to access this project, this service call will throw a 403 HTTP Exception.
    get_project_by_id_service(project_id, user)

async def _get_ai_status_payload():
    provider = (settings.AI_PROVIDER or "").lower().strip()
    if provider == "ollama":
        health = await check_ollama_health()
        is_ready = bool(health.get("available") and health.get("model_installed"))
        if is_ready:
            # Trigger non-blocking warm-up to eliminate cold starts
            asyncio.create_task(warmup_ollama())
        return {
            "provider": "ollama",
            "model": settings.OLLAMA_MODEL,
            "status": "ready" if is_ready else "unavailable",
            "health": health,
        }
    else:
        return {
            "provider": provider or "google-gemini",
            "model": settings.AI_MODEL,
            "status": "ready" if bool(settings.AI_API_KEY) else "unavailable",
            "health": {"available": bool(settings.AI_API_KEY)},
        }

@router.get("/status")
async def get_continuity_ai_status(project_id: str, current_user: UserOut = Depends(get_current_user)):
    """
    Returns AI provider and model status for the project and frontend status indicators.
    """
    check_project_access(project_id, current_user)
    return await _get_ai_status_payload()

@general_router.get("/status")
async def get_general_continuity_ai_status(current_user: UserOut = Depends(get_current_user)):
    """
    Returns AI provider status without requiring a specific project.
    """
    return await _get_ai_status_payload()

@router.get("/briefing", response_model=ContinuitySummaryOut)
async def get_continuity_data_briefing(project_id: str, current_user: UserOut = Depends(get_current_user)):
    """
    Returns a deterministic, verified database briefing derived directly from recorded tasks,
    activities, and risks (clearly distinguished from AI generation).
    """
    check_project_access(project_id, current_user)
    return await get_grounded_data_briefing_service(project_id, current_user)

@router.post("/generate", response_model=ContinuitySummaryOut)
async def generate_continuity_summary(project_id: str, current_user: UserOut = Depends(get_current_user)):
    """
    Generates a new AI Continuity Summary using verified project context.
    Access restricted to Project Managers and Developers assigned to the project.
    """
    check_project_access(project_id, current_user)
    return await generate_continuity_summary_service(project_id, current_user)

@router.put("/context", response_model=ContinuitySummaryOut)
async def update_continuity_project_context(
    project_id: str, 
    request: ProjectContextUpdateRequest, 
    current_user: UserOut = Depends(get_current_user)
):
    """
    Allows developers and managers to provide missing project context (description, tech stack, notes)
    to ground the AI developer handover when workspace records are sparse.
    """
    check_project_access(project_id, current_user)
    return await update_project_context_and_refresh_handover(project_id, request, current_user)

@router.post("/ask", response_model=AIAnswerResponse)
async def ask_continuity_question(project_id: str, request: AIQuestionRequest, current_user: UserOut = Depends(get_current_user)):
    """
    Asks the AI a specific question about the project based on verified context and optional conversation history.
    """
    check_project_access(project_id, current_user)
    
    question = (request.question or "").strip()
    messages = request.messages or []
    
    if not question and messages:
        for msg in reversed(messages):
            if msg.role == "user" and msg.content.strip():
                question = msg.content.strip()
                break
                
    if not question:
        raise HTTPException(status_code=400, detail="A question or at least one user message is required.")
        
    answer = await ask_continuity_question_service(project_id, question, current_user, messages=messages)
    return AIAnswerResponse(answer=answer)

@router.post("/stream")
async def stream_continuity_question(
    project_id: str, 
    request: AIQuestionRequest, 
    current_user: UserOut = Depends(get_current_user)
):
    """
    Streams response token chunks in real-time Server-Sent Events format.
    """
    check_project_access(project_id, current_user)
    question = (request.question or "").strip()
    messages = request.messages or []
    if not question and messages:
        for msg in reversed(messages):
            if msg.role == "user" and msg.content.strip():
                question = msg.content.strip()
                break
    if not question:
        raise HTTPException(status_code=400, detail="A question or at least one user message is required.")
        
    return StreamingResponse(
        stream_continuity_question_service(project_id, question, current_user, messages=messages),
        media_type="text/event-stream"
    )

@general_router.post("/ask", response_model=AIAnswerResponse)
async def ask_general_continuity_question(request: AIQuestionRequest, current_user: UserOut = Depends(get_current_user)):
    """
    Asks the AI assistant general programming, architecture, or workspace questions.
    """
    question = (request.question or "").strip()
    messages = request.messages or []
    if not question and messages:
        for msg in reversed(messages):
            if msg.role == "user" and msg.content.strip():
                question = msg.content.strip()
                break
    if not question:
        raise HTTPException(status_code=400, detail="A question or at least one user message is required.")
    answer = await ask_continuity_question_service("general", question, current_user, messages=messages)
    return AIAnswerResponse(answer=answer)

@general_router.post("/stream")
async def stream_general_continuity_question(
    request: AIQuestionRequest, 
    current_user: UserOut = Depends(get_current_user)
):
    """
    Streams general programming response token chunks in real-time Server-Sent Events format.
    """
    question = (request.question or "").strip()
    messages = request.messages or []
    if not question and messages:
        for msg in reversed(messages):
            if msg.role == "user" and msg.content.strip():
                question = msg.content.strip()
                break
    if not question:
        raise HTTPException(status_code=400, detail="A question or at least one user message is required.")
        
    return StreamingResponse(
        stream_continuity_question_service("general", question, current_user, messages=messages),
        media_type="text/event-stream"
    )



