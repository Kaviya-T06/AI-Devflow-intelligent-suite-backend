-- =============================================================
-- AI DevFlow — Auth Credentials Migration
-- Run this in: Supabase Dashboard → SQL Editor → New Query → RUN
--
-- WHY a separate table?
--   The PostgREST schema cache on Supabase sometimes refuses to
--   reflect ALTER TABLE changes to existing tables.
--   A brand-new CREATE TABLE is always picked up immediately.
--   This also follows proper separation-of-concerns:
--     profiles        = user identity / profile data (unchanged)
--     user_credentials = auth credentials (email + bcrypt hash)
-- =============================================================

CREATE TABLE IF NOT EXISTS public.user_credentials (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id    UUID        NOT NULL
                              REFERENCES public.profiles(id)
                              ON DELETE CASCADE,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup by email on every login
CREATE INDEX IF NOT EXISTS idx_user_credentials_email
    ON public.user_credentials(email);

-- Reload PostgREST schema cache so the table is immediately visible
NOTIFY pgrst, 'reload schema';

-- Verify: you should see 4 columns listed below
SELECT column_name, data_type, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name   = 'user_credentials'
ORDER  BY ordinal_position;
