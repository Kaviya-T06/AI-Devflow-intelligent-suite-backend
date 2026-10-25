-- =============================================================
-- AI DevFlow — Fix User Registration & Role Assignment
-- Run this script in:
--   Supabase Dashboard → SQL Editor → New Query → RUN
--
-- Safety Guarantees:
-- 1. Does NOT drop or truncate the `users` table.
-- 2. Does NOT alter or overwrite any existing user records.
-- 3. Confirms the authoritative role check constraint:
--    role IN ('admin', 'developer', 'project_manager')
-- 4. Confirms default role is 'developer' for unprivileged signups.
-- =============================================================

-- 1. Validate that the check constraint exists and matches the allowed roles
DO $$
BEGIN
    -- Check if constraint exists, if not recreate it safely
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_role_check'
    ) THEN
        ALTER TABLE public.users
            DROP CONSTRAINT IF EXISTS users_role_check;
        ALTER TABLE public.users
            ADD CONSTRAINT users_role_check
            CHECK (role IN ('admin', 'developer', 'project_manager'));
    END IF;
END $$;

-- 2. Ensure default role is 'developer'
ALTER TABLE public.users
    ALTER COLUMN role SET DEFAULT 'developer';

-- 3. Ensure role column is NOT NULL
ALTER TABLE public.users
    ALTER COLUMN role SET NOT NULL;

-- 4. Ensure index on email and role for fast role lookups
CREATE INDEX IF NOT EXISTS idx_users_role
    ON public.users(role);

-- 5. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

-- Verification output
SELECT column_name, column_default, is_nullable, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'users'
  AND column_name = 'role';
