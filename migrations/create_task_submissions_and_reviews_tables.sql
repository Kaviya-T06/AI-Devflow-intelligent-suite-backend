-- ==============================================================================
-- Migration: create_task_submissions_and_reviews_tables.sql
-- Description: Creates task_submissions and task_reviews tables for developer task
--              submissions and project manager reviews with full historical tracking.
-- ==============================================================================

-- 1. Create task_submissions table
CREATE TABLE IF NOT EXISTS public.task_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    submitted_by UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    work_summary TEXT NOT NULL,
    implementation_details TEXT NOT NULL,
    testing_performed TEXT NOT NULL,
    pull_request_url TEXT,
    deliverable_url TEXT,
    additional_comments TEXT,
    submission_number INT NOT NULL DEFAULT 1,
    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_task_submissions_task_id ON public.task_submissions(task_id);
CREATE INDEX IF NOT EXISTS idx_task_submissions_submitted_by ON public.task_submissions(submitted_by);
CREATE INDEX IF NOT EXISTS idx_task_submissions_created_at ON public.task_submissions(created_at);

-- 2. Create task_reviews table
CREATE TABLE IF NOT EXISTS public.task_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
    submission_id UUID NOT NULL REFERENCES public.task_submissions(id) ON DELETE CASCADE,
    reviewer_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    decision VARCHAR(50) NOT NULL, -- 'APPROVE', 'REQUEST_CHANGES', 'REJECT'
    review_comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

CREATE INDEX IF NOT EXISTS idx_task_reviews_task_id ON public.task_reviews(task_id);
CREATE INDEX IF NOT EXISTS idx_task_reviews_submission_id ON public.task_reviews(submission_id);
CREATE INDEX IF NOT EXISTS idx_task_reviews_reviewer_id ON public.task_reviews(reviewer_id);

-- 3. Row Level Security
ALTER TABLE public.task_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.task_reviews ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'Allow all for task_submissions via service role'
    ) THEN
        CREATE POLICY "Allow all for task_submissions via service role" ON public.task_submissions FOR ALL USING (true);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'Allow all for task_reviews via service role'
    ) THEN
        CREATE POLICY "Allow all for task_reviews via service role" ON public.task_reviews FOR ALL USING (true);
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
