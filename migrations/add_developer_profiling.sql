-- =============================================================
-- Migration: Add Developer Profiling and Task Requirements
-- =============================================================

-- Add developer profile fields to profiles table
ALTER TABLE public.profiles
ADD COLUMN IF NOT EXISTS skills JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS experience_years INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS preferred_role VARCHAR(255),
ADD COLUMN IF NOT EXISTS capacity_hours_per_week INTEGER DEFAULT 40,
ADD COLUMN IF NOT EXISTS relevant_experience JSONB DEFAULT '[]'::jsonb;

-- Add required skills to tasks table
ALTER TABLE public.tasks
ADD COLUMN IF NOT EXISTS required_skills JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS min_experience_years INTEGER DEFAULT 0;

-- Refresh schema cache
NOTIFY pgrst, 'reload schema';
