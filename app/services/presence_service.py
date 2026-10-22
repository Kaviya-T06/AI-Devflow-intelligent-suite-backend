"""
Presence Service — manages user presence, availability status, heartbeat,
real-time connection tracking, and WebSocket broadcasting.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from fastapi import HTTPException, WebSocket, status

from app.db.supabase_client import get_supabase_client
from app.schemas.auth import RoleEnum
from app.schemas.presence import (
    AvailabilityStatus,
    HeartbeatOut,
    PresenceOut,
    PresenceUpdate,
    TeamPresenceListOut,
)
from app.schemas.user import UserOut

logger = logging.getLogger("app.services.presence_service")

# Heartbeat timeout in seconds: after this period without heartbeats or active sockets, user is marked offline
HEARTBEAT_TIMEOUT_SECONDS = 45.0

# In-memory fallback store if database table is not yet migrated or during tests
_fallback_presence: Dict[str, Dict[str, Any]] = {}

# Active WebSocket connections per user ID
_active_connections: Dict[str, Set[WebSocket]] = {}
_lock = asyncio.Lock()


def _is_table_missing_error(exc: Exception) -> bool:
    """Check if exception indicates table is missing from schema cache."""
    err_str = str(exc)
    return (
        "PGRST205" in err_str
        or "public.user_presence" in err_str
        or "user_presence" in err_str.lower()
    )


def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        clean = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


class PresenceService:

    @classmethod
    def _get_active_conn_count(cls, user_id: str) -> int:
        return len(_active_connections.get(str(user_id), set()))

    @classmethod
    def _is_user_online(cls, user_id: str, last_seen_at_str: Optional[str]) -> bool:
        """
        Determines whether a user is currently online based on active socket connections
        or recent heartbeat within HEARTBEAT_TIMEOUT_SECONDS.
        """
        if cls._get_active_conn_count(user_id) > 0:
            return True

        if not last_seen_at_str:
            return False

        last_seen = _parse_iso(last_seen_at_str)
        if not last_seen:
            return False

        now = datetime.now(timezone.utc)
        return (now - last_seen).total_seconds() <= HEARTBEAT_TIMEOUT_SECONDS

    @classmethod
    def _check_and_expire(cls, row: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """
        Check if status_expires_at has passed.
        If passed, reset availability_status to 'available' and clear expiry.
        """
        expires_at = _parse_iso(row.get("status_expires_at"))
        if expires_at and expires_at < datetime.now(timezone.utc):
            row["availability_status"] = AvailabilityStatus.AVAILABLE.value
            row["status_expires_at"] = None
            row["status_message"] = None
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            return row, True
        return row, False

    @classmethod
    def _build_presence_out(
        cls,
        row: Dict[str, Any],
        user_info: Optional[Dict[str, Any]] = None,
    ) -> PresenceOut:
        """
        Computes effective presence:
        - If connection is offline: effective_status is 'offline'
        - If online: effective_status is user's availability_status (unless user manually set offline)
        """
        row, changed = cls._check_and_expire(row)
        user_id = str(row["user_id"])

        is_online = cls._is_user_online(user_id, row.get("last_seen_at"))
        status_val = row.get("availability_status", AvailabilityStatus.AVAILABLE.value)

        if not is_online:
            effective = AvailabilityStatus.OFFLINE.value
        elif status_val == AvailabilityStatus.OFFLINE.value:
            effective = AvailabilityStatus.OFFLINE.value
        else:
            effective = status_val

        return PresenceOut(
            user_id=user_id,
            user_name=user_info.get("name") if user_info else row.get("user_name"),
            user_email=user_info.get("email") if user_info else row.get("user_email"),
            user_role=user_info.get("role") if user_info else row.get("user_role"),
            availability_status=AvailabilityStatus(status_val),
            status_message=row.get("status_message"),
            status_expires_at=row.get("status_expires_at"),
            last_seen_at=row.get("last_seen_at", datetime.now(timezone.utc).isoformat()),
            is_online=is_online,
            effective_status=effective,
            updated_at=row.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )

    @classmethod
    def get_user_presence(cls, user_id: str) -> PresenceOut:
        """Fetch presence for a single user."""
        db = get_supabase_client()
        row: Optional[Dict[str, Any]] = None
        user_info: Optional[Dict[str, Any]] = None

        # Fetch user info for name/email/role
        try:
            u_resp = (
                db.table("users")
                .select("id, name, email, role")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
            if u_resp.data:
                user_info = u_resp.data[0]
        except Exception as e:
            logger.debug(f"Could not load user metadata for presence: {e}")

        try:
            p_resp = (
                db.table("user_presence")
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if p_resp.data:
                row = p_resp.data[0]
        except Exception as exc:
            if not _is_table_missing_error(exc):
                logger.error(f"Error querying user_presence: {exc}")

        if not row:
            if user_id in _fallback_presence:
                row = _fallback_presence[user_id]
            else:
                now_iso = datetime.now(timezone.utc).isoformat()
                row = {
                    "user_id": user_id,
                    "availability_status": AvailabilityStatus.AVAILABLE.value,
                    "status_message": None,
                    "status_expires_at": None,
                    "last_seen_at": now_iso,
                    "updated_at": now_iso,
                }
                _fallback_presence[user_id] = row

        presence = cls._build_presence_out(row, user_info)
        return presence

    @classmethod
    def update_user_presence(cls, user_id: str, update: PresenceUpdate) -> PresenceOut:
        """
        Update the authenticated user's availability status, custom message, and/or expiry.
        Validates user identity and persists changes.
        """
        db = get_supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Fetch current record
        current = cls.get_user_presence(user_id)
        new_status = (
            update.availability_status.value
            if update.availability_status is not None
            else current.availability_status.value
        )
        new_message = (
            update.status_message
            if update.status_message is not None
            else current.status_message
        )

        new_expires_at = current.status_expires_at
        if update.clear_expiry:
            new_expires_at = None
        elif update.status_expires_at is not None:
            new_expires_at = update.status_expires_at

        # Validate that expiry is in future if set
        if new_expires_at:
            exp_dt = _parse_iso(new_expires_at)
            if exp_dt and exp_dt <= datetime.now(timezone.utc):
                new_expires_at = None

        payload = {
            "user_id": user_id,
            "availability_status": new_status,
            "status_message": new_message,
            "status_expires_at": new_expires_at,
            "updated_at": now_iso,
        }

        # Save to DB or fallback
        persisted = False
        try:
            resp = (
                db.table("user_presence")
                .upsert(payload, on_conflict="user_id")
                .execute()
            )
            if resp.data:
                payload = resp.data[0]
                persisted = True
        except Exception as exc:
            if not _is_table_missing_error(exc):
                logger.error(f"Failed to upsert user_presence in Supabase: {exc}")

        _fallback_presence[user_id] = {
            **_fallback_presence.get(user_id, {}),
            **payload,
            "last_seen_at": _fallback_presence.get(user_id, {}).get("last_seen_at", now_iso),
        }

        updated_presence = cls.get_user_presence(user_id)
        # Broadcast live status update to WebSocket clients
        cls.broadcast_presence_sync(updated_presence)
        return updated_presence

    @classmethod
    def record_heartbeat(cls, user_id: str) -> HeartbeatOut:
        """
        Record heartbeat from an active client session / tab.
        Bumps last_seen_at and checks status expiry.
        """
        db = get_supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Get existing presence to check expiry
        current = cls.get_user_presence(user_id)
        curr_row = {
            "user_id": user_id,
            "availability_status": current.availability_status.value,
            "status_message": current.status_message,
            "status_expires_at": current.status_expires_at,
            "last_seen_at": now_iso,
            "updated_at": now_iso,
        }
        curr_row, expired = cls._check_and_expire(curr_row)

        update_data = {
            "last_seen_at": now_iso,
            "availability_status": curr_row["availability_status"],
            "status_message": curr_row["status_message"],
            "status_expires_at": curr_row["status_expires_at"],
            "updated_at": curr_row["updated_at"],
        }

        try:
            db.table("user_presence").upsert(
                {"user_id": user_id, **update_data}, on_conflict="user_id"
            ).execute()
        except Exception as exc:
            if not _is_table_missing_error(exc):
                logger.error(f"Error updating heartbeat in user_presence: {exc}")

        _fallback_presence[user_id] = {
            **_fallback_presence.get(user_id, {}),
            **update_data,
        }

        updated_presence = cls.get_user_presence(user_id)
        if expired:
            cls.broadcast_presence_sync(updated_presence)

        return HeartbeatOut(
            status="ok",
            last_seen_at=now_iso,
            is_online=True,
            availability_status=updated_presence.availability_status,
            effective_status=updated_presence.effective_status,
            status_expires_at=updated_presence.status_expires_at,
        )

    @classmethod
    def get_team_presence(
        cls,
        current_user: UserOut,
        role_filter: Optional[str] = None,
        availability_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> TeamPresenceListOut:
        """
        Retrieve team presence according to role-based access control.
        - Admin & Project Manager: full workspace team presence
        - Developer: colleagues across projects and workspace
        """
        db = get_supabase_client()

        # Load active users
        users_map: Dict[str, Dict[str, Any]] = {}
        try:
            q = db.table("users").select("id, name, email, role, is_active").eq("is_active", True)
            if role_filter and role_filter != "all":
                q = q.eq("role", role_filter.lower())
            res = q.execute()
            if res.data:
                for u in res.data:
                    users_map[str(u["id"])] = u
        except Exception as exc:
            logger.error(f"Error querying users for team presence: {exc}")

        # Load presence rows
        presence_map: Dict[str, Dict[str, Any]] = {}
        try:
            pres_res = db.table("user_presence").select("*").execute()
            if pres_res.data:
                for p in pres_res.data:
                    presence_map[str(p["user_id"])] = p
        except Exception as exc:
            if not _is_table_missing_error(exc):
                logger.error(f"Error querying user_presence table: {exc}")

        # Combine with fallback
        for uid, row in _fallback_presence.items():
            if uid not in presence_map:
                presence_map[uid] = row

        items: List[PresenceOut] = []
        for uid, uinfo in users_map.items():
            p_row = presence_map.get(
                uid,
                {
                    "user_id": uid,
                    "availability_status": AvailabilityStatus.AVAILABLE.value,
                    "status_message": None,
                    "status_expires_at": None,
                    "last_seen_at": uinfo.get("created_at", datetime.now(timezone.utc).isoformat()),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            item = cls._build_presence_out(p_row, uinfo)

            # Apply filters
            if search:
                s = search.lower()
                name_match = (item.user_name or "").lower().find(s) != -1
                email_match = (item.user_email or "").lower().find(s) != -1
                if not name_match and not email_match:
                    continue

            if availability_filter and availability_filter != "all":
                if item.effective_status.lower() != availability_filter.lower():
                    continue

            items.append(item)

        # Sort: online first, then by name
        items.sort(key=lambda x: (not x.is_online, x.user_name or ""))

        online_count = sum(1 for x in items if x.is_online)
        available_count = sum(1 for x in items if x.effective_status == AvailabilityStatus.AVAILABLE.value)
        busy_count = sum(1 for x in items if x.effective_status in (AvailabilityStatus.BUSY.value, AvailabilityStatus.DO_NOT_DISTURB.value))

        return TeamPresenceListOut(
            users=items,
            total=len(items),
            online_count=online_count,
            available_count=available_count,
            busy_count=busy_count,
        )

    @classmethod
    def is_dnd_active(cls, user_id: str) -> bool:
        """
        Check if user currently has an active Do Not Disturb availability status.
        """
        presence = cls.get_user_presence(str(user_id))
        return presence.effective_status == AvailabilityStatus.DO_NOT_DISTURB.value

    # -----------------------------------------------------------------------
    # WebSocket Connection Management
    # -----------------------------------------------------------------------

    @classmethod
    async def register_websocket(cls, user_id: str, websocket: WebSocket) -> None:
        """Register a new active WebSocket connection for a user."""
        uid = str(user_id)
        if uid not in _active_connections:
            _active_connections[uid] = set()
        _active_connections[uid].add(websocket)

        # Update last_seen_at
        now_iso = datetime.now(timezone.utc).isoformat()
        if uid in _fallback_presence:
            _fallback_presence[uid]["last_seen_at"] = now_iso
            _fallback_presence[uid]["updated_at"] = now_iso

        presence = cls.get_user_presence(uid)
        logger.info(f"WebSocket registered for user {uid}. Total tabs: {len(_active_connections[uid])}")
        cls.broadcast_presence_sync(presence)

    @classmethod
    async def unregister_websocket(cls, user_id: str, websocket: WebSocket) -> None:
        """Unregister a WebSocket connection. If 0 connections remain, mark offline."""
        uid = str(user_id)
        if uid in _active_connections:
            _active_connections[uid].discard(websocket)
            if not _active_connections[uid]:
                del _active_connections[uid]
                # Update last seen to now
                now_iso = datetime.now(timezone.utc).isoformat()
                if uid in _fallback_presence:
                    _fallback_presence[uid]["last_seen_at"] = now_iso
                try:
                    db = get_supabase_client()
                    db.table("user_presence").update({"last_seen_at": now_iso}).eq("user_id", uid).execute()
                except Exception:
                    pass
                logger.info(f"All connections closed for user {uid}. User is now offline.")
                presence = cls.get_user_presence(uid)
                cls.broadcast_presence_sync(presence)
            else:
                logger.info(f"WebSocket closed for tab of user {uid}. Remaining tabs: {len(_active_connections[uid])}")

    @classmethod
    def broadcast_presence_sync(cls, presence: PresenceOut) -> None:
        """Schedule asynchronous broadcast of presence change."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(cls._broadcast_presence_async(presence))
        except RuntimeError:
            # If no running event loop, ignore broadcast (e.g. in synchronous test)
            pass

    @classmethod
    async def _broadcast_presence_async(cls, presence: PresenceOut) -> None:
        """Broadcast presence update to all connected WebSocket clients."""
        payload = {
            "type": "PRESENCE_UPDATE",
            "data": presence.model_dump(),
        }
        dead_sockets: List[Tuple[str, WebSocket]] = []

        for uid, sockets in list(_active_connections.items()):
            for ws in list(sockets):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead_sockets.append((uid, ws))

        for uid, ws in dead_sockets:
            if uid in _active_connections:
                _active_connections[uid].discard(ws)
