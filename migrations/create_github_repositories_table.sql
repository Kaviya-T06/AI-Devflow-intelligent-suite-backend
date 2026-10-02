CREATE TABLE IF NOT EXISTS public.project_github_repositories (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES public.projects(id) ON DELETE CASCADE,
    github_repository_id VARCHAR(255),
    owner VARCHAR(255) NOT NULL,
    repository_name VARCHAR(255) NOT NULL,
    full_name VARCHAR(512) NOT NULL,
    html_url VARCHAR(1024),
    default_branch VARCHAR(255) DEFAULT 'main',
    connected_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_github_repo_project_id ON public.project_github_repositories(project_id);
