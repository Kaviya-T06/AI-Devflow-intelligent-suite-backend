"""
Presence Endpoints — manages authenticated user presence, heartbeat, team presence,
and live WebSocket connections.
"""
import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from jose import JWTError

from app.api.deps import get_current_user
from app.core.security import decode_access_token
from app.db.supabase_client import get_supabase_client
from app.schemas.presence import (
    HeartbeatOut,
    PresenceOut,
    PresenceUpdate,
    TeamPresenceListOut,
)
from app.schemas.user import UserOut
from app.services.presence_service import PresenceService

logger = logging.getLogger("app.api.v1.endpoints.presence")

router = APIRouter(prefix="/presence", tags=["presence"])


@router.get("/me", response_model=PresenceOut)
def get_my_presence(
    current_user: UserOut = Depends(get_current_user),
) -> PresenceOut:
    """
    Get presence and availability information for the authenticated user.
    """
    return PresenceService.get_user_presence(str(current_user.id))


@router.put("/me", response_model=PresenceOut)
@router.patch("/me", response_model=PresenceOut)
def update_my_presence(
    update: PresenceUpdate,
    current_user: UserOut = Depends(get_current_user),
) -> PresenceOut:
    """
    Update own presence, availability status, message, and/or expiry.
    Server enforces that only the authenticated user's own status is updated.
    """
    return PresenceService.update_user_presence(str(current_user.id), update)


@router.post("/heartbeat", response_model=HeartbeatOut)
def send_heartbeat(
    current_user: UserOut = Depends(get_current_user),
) -> HeartbeatOut:
    """
    Send an active heartbeat to keep online connection state alive and
    evaluate any scheduled status expiry.
    """
    return PresenceService.record_heartbeat(str(current_user.id))


@router.get("/team", response_model=TeamPresenceListOut)
def get_team_presence(
    role: Optional[str] = Query(None, description="Filter by role: admin, project_manager, developer"),
    availability: Optional[str] = Query(None, description="Filter by availability: available, busy, do_not_disturb, away, offline"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    current_user: UserOut = Depends(get_current_user),
) -> TeamPresenceListOut:
    """
    Retrieve live team presence.
    Accessible to all authenticated roles with appropriate RBAC scoping.
    """
    return PresenceService.get_team_presence(
        current_user=current_user,
        role_filter=role,
        availability_filter=availability,
        search=search,
    )


@router.get("/users/{user_id}", response_model=PresenceOut)
def get_single_user_presence(
    user_id: str,
    current_user: UserOut = Depends(get_current_user),
) -> PresenceOut:
    """
    Get presence for a specific user.
    """
    return PresenceService.get_user_presence(user_id)


@router.websocket("/ws")
async def presence_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
):
    """
    Live WebSocket connection for presence synchronization and heartbeat streaming.
    Requires Bearer JWT token in query parameter (?token=<token>).
    """
    await websocket.accept()

    # Authenticate token
    if not token:
        logger.warning("Presence WS: Rejected connection with missing token.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        payload = decode_access_token(token.strip())
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except (JWTError, Exception) as err:
        logger.warning(f"Presence WS: JWT authentication failed: {err}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Verify user exists in DB
    try:
        db = get_supabase_client()
        user_row = db.table("users").select("id, is_active").eq("id", user_id).limit(1).execute()
        if not user_row.data or not user_row.data[0].get("is_active", True):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    except Exception as e:
        logger.debug(f"User validation fallback for presence WS: {e}")

    await PresenceService.register_websocket(user_id, websocket)

    # Send initial current presence
    try:
        initial = PresenceService.get_user_presence(user_id)
        await websocket.send_json({
            "type": "INITIAL_PRESENCE",
            "data": initial.model_dump(),
        })
    except Exception as exc:
        logger.error(f"Error sending initial presence on WS: {exc}")

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type", "").upper()

            if msg_type in ("HEARTBEAT", "PING"):
                hb = PresenceService.record_heartbeat(user_id)
                await websocket.send_json({
                    "type": "HEARTBEAT_ACK",
                    "data": hb.model_dump(),
                })
            elif msg_type == "UPDATE_STATUS":
                data = msg.get("data", {})
                update_obj = PresenceUpdate(**data)
                updated = PresenceService.update_user_presence(user_id, update_obj)
                await websocket.send_json({
                    "type": "STATUS_UPDATED",
                    "data": updated.model_dump(),
                })
            else:
                await websocket.send_json({"type": "ACK"})

    except WebSocketDisconnect:
        logger.info(f"Presence WS disconnected cleanly for user {user_id}")
    except Exception as exc:
        logger.warning(f"Presence WS connection error for user {user_id}: {exc}")
    finally:
        await PresenceService.unregister_websocket(user_id, websocket)
