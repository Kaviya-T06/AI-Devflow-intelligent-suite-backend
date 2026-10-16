-- ==============================================================================
-- Migration: add_branch_developer_assignments.sql
-- Description: Create project_branch_assignments table to link developers and
--              tasks to discovered GitHub branches per project.
-- Safe migration: preserves all existing data and records.
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.project_branch_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    branch_name TEXT NOT NULL,
    developer_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    task_id UUID REFERENCES public.tasks(id) ON DELETE SET NULL,
    assigned_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_project_branch UNIQUE (project_id, branch_name)
);

CREATE INDEX IF NOT EXISTS idx_branch_assignments_project ON public.project_branch_assignments(project_id);
CREATE INDEX IF NOT EXISTS idx_branch_assignments_dev ON public.project_branch_assignments(developer_id);

-- Enable RLS
ALTER TABLE public.project_branch_assignments ENABLE ROW LEVEL SECURITY;

-- Allow project members and managers to view assignments
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'Users can view branch assignments for their projects'
    ) THEN
        CREATE POLICY "Users can view branch assignments for their projects"
            ON public.project_branch_assignments FOR SELECT
            USING (
                EXISTS (
                    SELECT 1 FROM public.projects 
                    WHERE public.projects.id = public.project_branch_assignments.project_id 
                    AND (
                        public.projects.project_manager_id = auth.uid()
                        OR EXISTS (
                            SELECT 1 FROM public.tasks 
                            WHERE public.tasks.project_id = public.project_branch_assignments.project_id 
                            AND public.tasks.assigned_to = auth.uid()
                        )
                    )
                )
                OR EXISTS (
                    SELECT 1 FROM public.users 
                    WHERE id = auth.uid() AND LOWER(role) IN ('admin', 'manager', 'project_manager')
                )
            );
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
