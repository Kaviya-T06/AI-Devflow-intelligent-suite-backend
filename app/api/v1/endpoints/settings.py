"""
Settings router — placeholder endpoints.
No database connection. Returns mock responses for Swagger testing.
"""
from fastapi import APIRouter

from app.schemas.common import SettingsOut, SettingsUpdateRequest

router = APIRouter(prefix="/settings", tags=["Settings"])

# ---------------------------------------------------------------------------
# Placeholder defaults
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS = SettingsOut(
    notifications_enabled=True,
    email_alerts=True,
    theme="dark",
    language="en",
    timezone="UTC",
    ai_suggestions_enabled=True,
    weekly_report_enabled=True,
)


@router.get(
    "",
    response_model=SettingsOut,
    summary="Get current user settings",
)
async def get_settings():
    """
    Retrieve the current application settings for the authenticated user.

    Returns preferences such as **theme**, **language**, **timezone**,
    notification preferences, and AI feature toggles.

    > **Note:** Returns placeholder default settings.
    Database integration coming in a future milestone.
    """
    return _DEFAULT_SETTINGS


@router.patch(
    "",
    response_model=SettingsOut,
    summary="Update user settings",
)
async def update_settings(payload: SettingsUpdateRequest):
    """
    Partially update the authenticated user's application settings.

    Only include the fields you want to change. Unset fields are left unchanged.

    > **Note:** This is a placeholder — no settings are persisted yet.
    """
    updated = _DEFAULT_SETTINGS.model_dump()
    patch_data = payload.model_dump(exclude_none=True)
    updated.update(patch_data)
    return SettingsOut(**updated)
