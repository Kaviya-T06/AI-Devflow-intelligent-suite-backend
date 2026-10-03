-- ==============================================================================
-- Alter Activity Logs for Project History Milestone
-- ==============================================================================

-- 1. Add project_id to link activities to specific projects
ALTER TABLE public.activity_logs
ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES public.projects(id) ON DELETE CASCADE;

-- 2. Add metadata for rich event context
ALTER TABLE public.activity_logs
ADD COLUMN IF NOT EXISTS metadata JSONB;

-- 3. Create index for fast project history lookups
CREATE INDEX IF NOT EXISTS idx_activity_logs_project_id ON public.activity_logs(project_id);

-- 4. Reload schema cache
NOTIFY pgrst, 'reload schema';
