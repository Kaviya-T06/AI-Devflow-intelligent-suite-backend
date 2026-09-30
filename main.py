"""
AI DevFlow Intelligence Suite — Backend Application
Main FastAPI application entry point.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.router import api_router

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_application() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AI DevFlow Intelligence Suite API",
        description=(
            "Backend API for the AI DevFlow Intelligence Suite — "
            "a software development workflow intelligence platform."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={"persistAuthorization": True},
    )

    # ------------------------------------------------------------------
    # CORS Middleware
    # Only allow requests from the configured frontend origin(s).
    # ------------------------------------------------------------------
    origins = [settings.FRONTEND_URL]
    if settings.APP_ENV == "development":
        # Allow Vite's default port as well during local development
        origins += [
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_application()


# ---------------------------------------------------------------------------
# Root health-check (also available at /api/v1/health)
# ---------------------------------------------------------------------------

@app.get("/", tags=["Root"])
async def root():
    """Root endpoint — basic connectivity check."""
    return {
        "message": "AI DevFlow Intelligence Suite API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }
