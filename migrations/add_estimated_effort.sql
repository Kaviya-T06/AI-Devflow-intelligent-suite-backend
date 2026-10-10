-- ==============================================================================
-- Add estimated_effort to tasks Table
-- ==============================================================================

-- Add the column, allowing NULL for tasks whose effort is unknown.
ALTER TABLE public.tasks 
ADD COLUMN IF NOT EXISTS estimated_effort NUMERIC(8,2) DEFAULT NULL;

-- Ensure estimated_effort cannot be negative.
ALTER TABLE public.tasks
ADD CONSTRAINT check_estimated_effort_positive CHECK (estimated_effort >= 0);
