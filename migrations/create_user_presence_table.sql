-- =============================================================
-- AI DevFlow — Create `user_presence` table
-- Run this ENTIRE script in:
--   Supabase Dashboard → SQL Editor → New Query → RUN
-- =============================================================

CREATE TABLE IF NOT EXISTS public.user_presence (
    user_id             UUID        PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    availability_status TEXT        NOT NULL DEFAULT 'available'
                                    CHECK (availability_status IN ('available', 'busy', 'do_not_disturb', 'away', 'offline')),
    status_message      TEXT,
    status_expires_at   TIMESTAMPTZ,
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_user_presence_last_seen
    ON public.user_presence(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_user_presence_availability
    ON public.user_presence(availability_status);

-- Auto-bump updated_at on change
DROP TRIGGER IF EXISTS user_presence_set_updated_at ON public.user_presence;
CREATE TRIGGER user_presence_set_updated_at
    BEFORE UPDATE ON public.user_presence
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Populate existing users with default presence
INSERT INTO public.user_presence (user_id, availability_status, last_seen_at, updated_at)
SELECT id, 'available', NOW(), NOW()
FROM public.users
ON CONFLICT (user_id) DO NOTHING;

-- Tell PostgREST to reload its schema cache immediately
NOTIFY pgrst, 'reload schema';

-- Verification
SELECT column_name, data_type, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name   = 'user_presence'
ORDER  BY ordinal_position;
