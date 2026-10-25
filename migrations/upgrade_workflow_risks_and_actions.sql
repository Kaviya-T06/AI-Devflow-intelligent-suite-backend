-- ==============================================================================
-- Upgrade Workflow Risks Table Migration
-- Production Risk Detection, Investigation, Corrective Action, Escalation & Resolution
-- ==============================================================================

-- Safely extend public.workflow_risks with corrective action and investigation fields
ALTER TABLE public.workflow_risks 
    ADD COLUMN IF NOT EXISTS task_due_date TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS detection_rule VARCHAR(100),
    ADD COLUMN IF NOT EXISTS detection_reason TEXT,
    ADD COLUMN IF NOT EXISTS severity_reason TEXT,
    ADD COLUMN IF NOT EXISTS recovery_deadline TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS corrective_action_plan TEXT,
    ADD COLUMN IF NOT EXISTS action_owner_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS action_status VARCHAR(30) DEFAULT 'NOT_STARTED',
    ADD COLUMN IF NOT EXISTS is_escalated BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS escalated_to VARCHAR(50),
    ADD COLUMN IF NOT EXISTS escalated_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS escalation_reason TEXT,
    ADD COLUMN IF NOT EXISTS resolution_notes TEXT,
    ADD COLUMN IF NOT EXISTS resolved_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS verified_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS verification_notes TEXT,
    ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS recurrence_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS comments JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS history JSONB DEFAULT '[]'::jsonb;

-- Add performance indexes for risk lookups and action tracking
CREATE INDEX IF NOT EXISTS idx_workflow_risks_owner ON public.workflow_risks(owner_id);
CREATE INDEX IF NOT EXISTS idx_workflow_risks_action_status ON public.workflow_risks(action_status);
CREATE INDEX IF NOT EXISTS idx_workflow_risks_is_escalated ON public.workflow_risks(is_escalated);
CREATE INDEX IF NOT EXISTS idx_workflow_risks_recovery_deadline ON public.workflow_risks(recovery_deadline);
CREATE INDEX IF NOT EXISTS idx_workflow_risks_is_recurring ON public.workflow_risks(is_recurring);

-- Reload postgrest schema cache
NOTIFY pgrst, 'reload schema';
