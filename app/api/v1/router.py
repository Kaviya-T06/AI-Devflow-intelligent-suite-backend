"""
Central API v1 router — registers all endpoint modules.
"""
from fastapi import APIRouter

# ---------------------------------------------------------------------------
# Existing routers (Milestone 1)
# ---------------------------------------------------------------------------
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.profiles import router as profiles_router

# ---------------------------------------------------------------------------
# New routers (Milestone 2 — structure + Swagger only, no DB yet)
# ---------------------------------------------------------------------------
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.projects import router as projects_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.activity import router as activity_router
from app.api.v1.endpoints.workflow_risks import router as workflow_risks_router
from app.api.v1.endpoints.repositories import router as repositories_router
from app.api.v1.endpoints.settings import router as settings_router
from app.api.v1.endpoints.dashboard import router as dashboard_router

api_router = APIRouter()

# Existing
api_router.include_router(health_router)
api_router.include_router(profiles_router)

# New — placeholder endpoints
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(dashboard_router)
api_router.include_router(projects_router)
api_router.include_router(tasks_router)
api_router.include_router(activity_router)
api_router.include_router(workflow_risks_router)
api_router.include_router(repositories_router)
api_router.include_router(settings_router)

# ---------------------------------------------------------------------------
# Future routers (not yet implemented)
# ---------------------------------------------------------------------------
# from app.api.v1.endpoints.github import router as github_router
# from app.api.v1.endpoints.jira import router as jira_router
# from app.api.v1.endpoints.analytics import router as analytics_router
# from app.api.v1.endpoints.ai_insights import router as ai_router
