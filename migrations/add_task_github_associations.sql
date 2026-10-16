-- ==============================================================================
-- AI DevFlow Intelligence Suite — Task GitHub Association & Workflow Schema
-- ==============================================================================

-- 1. Ensure project_id and metadata exist on activity_logs
ALTER TABLE public.activity_logs
ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES public.projects(id) ON DELETE CASCADE;

ALTER TABLE public.activity_logs
ADD COLUMN IF NOT EXISTS metadata JSONB;

-- 2. Backfill null project_id on project entity activities
UPDATE public.activity_logs
SET project_id = entity_id::uuid
WHERE entity_type = 'project' AND project_id IS NULL AND entity_id IS NOT NULL;

-- 3. Create index for fast project activity and history lookups
CREATE INDEX IF NOT EXISTS idx_activity_logs_project_id ON public.activity_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_activity_logs_entity_id ON public.activity_logs(entity_id);
CREATE INDEX IF NOT EXISTS idx_activity_logs_action ON public.activity_logs(action);

-- 4. Reload schema cache for PostgREST
NOTIFY pgrst, 'reload schema';
