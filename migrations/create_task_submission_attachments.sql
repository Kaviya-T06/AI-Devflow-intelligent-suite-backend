-- ==============================================================================
-- Migration: create_task_submission_attachments.sql
-- Description: Creates task_submission_attachments table for developer file uploads
--              associated with task submissions and review revisions.
-- Storage Bucket: task-review-attachments (private)
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.task_submission_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    submission_id UUID NOT NULL REFERENCES public.task_submissions(id) ON DELETE CASCADE,
    uploaded_by UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    storage_path TEXT NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    size BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_task_submission_attachments_task_id ON public.task_submission_attachments(task_id);
CREATE INDEX IF NOT EXISTS idx_task_submission_attachments_submission_id ON public.task_submission_attachments(submission_id);
CREATE INDEX IF NOT EXISTS idx_task_submission_attachments_uploaded_by ON public.task_submission_attachments(uploaded_by);

-- Enable Row Level Security
ALTER TABLE public.task_submission_attachments ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'Allow all for task_submission_attachments via service role'
    ) THEN
        CREATE POLICY "Allow all for task_submission_attachments via service role" ON public.task_submission_attachments FOR ALL USING (true);
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
