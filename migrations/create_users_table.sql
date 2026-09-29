-- =============================================================
-- AI DevFlow — Create `users` table
-- Run this ENTIRE script in:
--   Supabase Dashboard → SQL Editor → New Query → RUN
--
-- This is a brand-new table. It is the ONLY table used for
-- authentication (register + login). The `profiles` table is
-- NOT touched by this migration.
-- =============================================================

CREATE TABLE IF NOT EXISTS public.users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT        NOT NULL,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL DEFAULT 'developer'
                              CHECK (role IN ('admin', 'developer', 'project_manager')),
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast login lookups by email
CREATE INDEX IF NOT EXISTS idx_users_email
    ON public.users(email);

-- Auto-bump updated_at on every row change
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS users_set_updated_at ON public.users;
CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Tell PostgREST to reload its schema cache immediately
NOTIFY pgrst, 'reload schema';

-- Verify — you should see all 8 columns listed below
SELECT column_name, data_type, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name   = 'users'
ORDER  BY ordinal_position;
