-- ==============================================================================
-- Workflow Risks Table Migration
-- ==============================================================================

-- Drop existing if needed
DROP TABLE IF EXISTS public.workflow_risks CASCADE;

-- Create the workflow_risks table
CREATE TABLE public.workflow_risks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES public.projects(id) ON DELETE CASCADE,
    task_id UUID REFERENCES public.tasks(id) ON DELETE CASCADE,
    user_id UUID REFERENCES public.users(id) ON DELETE CASCADE,
    risk_type VARCHAR(50) NOT NULL,
    level VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'OPEN',
    is_resolved BOOLEAN DEFAULT FALSE,
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Indexes for querying
CREATE INDEX idx_workflow_risks_project_id ON public.workflow_risks(project_id);
CREATE INDEX idx_workflow_risks_task_id ON public.workflow_risks(task_id);
CREATE INDEX idx_workflow_risks_status ON public.workflow_risks(status);
CREATE INDEX idx_workflow_risks_is_resolved ON public.workflow_risks(is_resolved);

-- Optional RLS
ALTER TABLE public.workflow_risks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for workflow risks" ON public.workflow_risks FOR ALL USING (true);
