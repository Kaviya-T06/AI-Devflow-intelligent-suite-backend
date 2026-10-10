-- ==============================================================================
-- Notifications Table Migration
-- ==============================================================================

-- Drop existing table if needed
DROP TABLE IF EXISTS public.notifications CASCADE;

-- Create the notifications table
CREATE TABLE public.notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    project_id UUID REFERENCES public.projects(id) ON DELETE CASCADE,
    task_id UUID REFERENCES public.tasks(id) ON DELETE CASCADE,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Fast lookups by recipient and read status
CREATE INDEX idx_notifications_recipient_id ON public.notifications(recipient_id);
CREATE INDEX idx_notifications_recipient_read ON public.notifications(recipient_id, is_read);
CREATE INDEX idx_notifications_created_at ON public.notifications(created_at DESC);

-- Enable RLS
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for notifications via service role" ON public.notifications FOR ALL USING (true);

-- Auto-bump updated_at on row update
DROP TRIGGER IF EXISTS notifications_set_updated_at ON public.notifications;
CREATE TRIGGER notifications_set_updated_at
    BEFORE UPDATE ON public.notifications
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Notify PostgREST to reload schema cache immediately
NOTIFY pgrst, 'reload schema';
