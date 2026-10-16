-- ==============================================================================
-- Migration: add_github_user_authorization.sql
-- Description: Enable secure GitHub token and username authorization per user,
--              and allow assigned developers to view project repository metadata.
-- ==============================================================================

-- 1. Safely add github_token and github_username to users and profiles
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS github_token TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS github_username VARCHAR(255);

ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS github_token TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS github_username VARCHAR(255);

-- 2. Update RLS policy so assigned developers can also view connected repository
DROP POLICY IF EXISTS "Users can view github repos for projects they have access to" ON public.project_github_repositories;

CREATE POLICY "Users can view github repos for projects they have access to"
    ON public.project_github_repositories FOR SELECT
    USING (
        -- Project Manager of the project
        EXISTS (
            SELECT 1 FROM public.projects 
            WHERE public.projects.id = public.project_github_repositories.project_id 
            AND public.projects.project_manager_id = auth.uid()
        )
        OR
        -- Admins and Managers
        EXISTS (
            SELECT 1 FROM public.users 
            WHERE id = auth.uid() 
            AND LOWER(role) IN ('admin', 'manager', 'project_manager')
        )
        OR
        -- Assigned Developers on the project
        EXISTS (
            SELECT 1 FROM public.tasks 
            WHERE public.tasks.project_id = public.project_github_repositories.project_id 
            AND public.tasks.assigned_to = auth.uid()
        )
    );

-- 3. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
