-- =============================================================
-- AI DevFlow — Standardize `users` and Create `projects` Table
-- Run this ENTIRE script in:
--   Supabase Dashboard → SQL Editor → New Query → RUN
-- =============================================================

-- Step 1: Ensure public.users.id is typed as UUID
DO $$
DECLARE
    users_id_type text;
BEGIN
    SELECT data_type INTO users_id_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'users'
      AND column_name = 'id';

    IF users_id_type IS NOT NULL AND users_id_type <> 'uuid' THEN
        -- Remove legacy identity/default if present
        ALTER TABLE public.users ALTER COLUMN id DROP IDENTITY IF EXISTS;
        BEGIN
            ALTER TABLE public.users ALTER COLUMN id DROP DEFAULT;
        EXCEPTION WHEN OTHERS THEN
            NULL;
        END;
        -- Convert users.id to UUID
        ALTER TABLE public.users ALTER COLUMN id TYPE UUID USING (
            CASE
                WHEN id::text ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                THEN id::text::uuid
                ELSE gen_random_uuid()
            END
        );
        ALTER TABLE public.users ALTER COLUMN id SET DEFAULT gen_random_uuid();
    END IF;
END $$;

-- Step 2: Drop legacy/conflicting projects table
DROP TABLE IF EXISTS public.projects CASCADE;

-- Step 3: Create the clean projects table
CREATE TABLE public.projects (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name               TEXT        NOT NULL,
    description        TEXT,
    status             TEXT        NOT NULL DEFAULT 'planning'
                                   CHECK (status IN ('planning', 'active', 'on_hold', 'completed', 'archived')),
    project_manager_id UUID        REFERENCES public.users(id) ON DELETE SET NULL,
    progress           INTEGER     NOT NULL DEFAULT 0
                                   CHECK (progress >= 0 AND progress <= 100),
    start_date         DATE,
    end_date           DATE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT check_projects_name CHECK (char_length(trim(name)) > 0),
    CONSTRAINT check_projects_dates CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);

-- Step 4: Create Indexes for performant filtering and joins
CREATE INDEX idx_projects_status ON public.projects(status);
CREATE INDEX idx_projects_project_manager_id ON public.projects(project_manager_id);
CREATE INDEX idx_projects_created_at ON public.projects(created_at DESC);

-- Step 5: Auto-update updated_at timestamp trigger
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS projects_set_updated_at ON public.projects;
CREATE TRIGGER projects_set_updated_at
    BEFORE UPDATE ON public.projects
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Step 6: Notify PostgREST to reload schema cache immediately
NOTIFY pgrst, 'reload schema';

-- Step 7: Verification query (lists columns of both users and projects tables)
SELECT table_name, column_name, data_type, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name IN ('users', 'projects')
ORDER  BY table_name, ordinal_position;
