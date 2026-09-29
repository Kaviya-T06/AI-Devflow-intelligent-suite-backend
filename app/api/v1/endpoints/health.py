"""
Health check router.
"""
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.profile import HealthResponse

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse, summary="Health Check")
async def health_check():
    """
    Returns the current health status of the API.

    **Always returns 200 OK** if the server is running.
    Used by frontend to verify backend connectivity.
    """
    return HealthResponse(
        status="healthy",
        environment=settings.APP_ENV,
        version="1.0.0",
    )
