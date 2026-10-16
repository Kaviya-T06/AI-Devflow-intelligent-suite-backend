-- ==============================================================================
-- Migration: Add estimated_hours to tasks and capacity_hours_per_week to users
-- Safe migration: preserves all existing data and records.
-- ==============================================================================

-- 1. Add estimated_hours column to tasks table if it does not already exist
ALTER TABLE public.tasks 
ADD COLUMN IF NOT EXISTS estimated_hours NUMERIC(8,2) DEFAULT NULL;

-- 2. If estimated_effort has values, synchronize them to estimated_hours
UPDATE public.tasks 
SET estimated_hours = estimated_effort 
WHERE estimated_hours IS NULL AND estimated_effort IS NOT NULL;

-- 3. If estimated_hours has values, synchronize to estimated_effort for backwards compatibility
UPDATE public.tasks 
SET estimated_effort = estimated_hours 
WHERE estimated_effort IS NULL AND estimated_hours IS NOT NULL;

-- 4. Ensure non-negative constraint on estimated_hours
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'check_estimated_hours_positive'
    ) THEN
        ALTER TABLE public.tasks ADD CONSTRAINT check_estimated_hours_positive CHECK (estimated_hours >= 0);
    END IF;
END $$;

-- 5. Add capacity_hours_per_week to users table if not exists (profiles already has it)
ALTER TABLE public.users 
ADD COLUMN IF NOT EXISTS capacity_hours_per_week NUMERIC(8,2) DEFAULT NULL;

-- 6. Refresh PostgREST schema cache
NOTIFY pgrst, 'reload schema';
