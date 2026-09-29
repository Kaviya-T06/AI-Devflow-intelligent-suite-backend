"""
Supabase client factory — used by backend services.
Uses the SERVICE ROLE key for admin operations.
Never expose this key to the frontend.
"""
from functools import lru_cache

from supabase import create_client, Client

from app.core.config import settings


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a cached Supabase client using the service-role key."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
        )
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


@lru_cache(maxsize=1)
def get_supabase_anon_client() -> Client:
    """Return a cached Supabase client using the anon key (public operations)."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env"
        )
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
