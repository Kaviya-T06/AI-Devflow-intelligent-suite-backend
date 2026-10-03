import json
from datetime import datetime, timezone
from fastapi import HTTPException
from app.db.supabase_client import get_supabase_client
from app.schemas.user import UserOut
from app.schemas.continuity import ContinuitySummaryOut
from app.services.ai_service import ask_llm
from app.services.activity_service import log_activity
import httpx

def _db():
    return get_supabase_client()

async def _fetch_github_data(project_id: str):
    try:
        repo_resp = _db().table("project_github_repositories").select("*").eq("project_id", project_id).execute()
        if not repo_resp.data:
            return None
        repo = repo_resp.data[0]
        full_name = repo['full_name']
        
        async with httpx.AsyncClient(headers={"Accept": "application/vnd.github.v3+json"}) as client:
            commits_res = await client.get(f"https://api.github.com/repos/{full_name}/commits?per_page=5")
            commits = commits_res.json() if commits_res.status_code == 200 else []
            
            prs_res = await client.get(f"https://api.github.com/repos/{full_name}/pulls?state=all&per_page=5")
            prs = prs_res.json() if prs_res.status_code == 200 else []
            
            issues_res = await client.get(f"https://api.github.com/repos/{full_name}/issues?state=all&per_page=5")
            issues = [i for i in (issues_res.json() if issues_res.status_code == 200 else []) if "pull_request" not in i]
            
        return {
            "repository": full_name,
            "commits": [{"message": c["commit"]["message"], "author": c["commit"]["author"]["name"], "date": c["commit"]["author"]["date"]} for c in commits if isinstance(c, dict) and "commit" in c],
            "pull_requests": [{"title": p["title"], "state": p["state"], "author": p["user"]["login"]} for p in prs if isinstance(p, dict) and "title" in p],
            "issues": [{"title": i["title"], "state": i["state"]} for i in issues if isinstance(i, dict) and "title" in i]
        }
    except Exception:
        return None

async def _build_project_context(project_id: str) -> str:
    # 1. Fetch Project
    proj_resp = _db().table("projects").select("*, users(name)").eq("id", project_id).execute()
    if not proj_resp.data:
        raise HTTPException(status_code=404, detail="Project not found")
    project = proj_resp.data[0]
    
    # 2. Fetch Tasks
    tasks_resp = _db().table("tasks").select("*, users(name)").eq("project_id", project_id).execute()
    tasks = tasks_resp.data or []
    
    # 3. Fetch Activity
    activity_resp = _db().table("activity_logs").select("*, users(name)").eq("entity_id", project_id).order("created_at", desc=True).limit(20).execute()
    activities = activity_resp.data or []
    
    # 4. Fetch GitHub
    github_data = await _fetch_github_data(project_id)
    
    # Structure Context
    context = []
    context.append(f"Project Name: {project.get('name')}")
    context.append(f"Description: {project.get('description')}")
    context.append(f"Status: {project.get('status')} - Progress: {project.get('progress')}%")
    pm_name = project.get("users", {}).get("name") if isinstance(project.get("users"), dict) else (project.get("users")[0].get("name") if isinstance(project.get("users"), list) and len(project.get("users")) > 0 else "Unknown")
    context.append(f"Project Manager: {pm_name}")
    
    context.append("\nTASKS:")
    for t in tasks:
        dev_name = t.get("users", {}).get("name") if isinstance(t.get("users"), dict) else (t.get("users")[0].get("name") if isinstance(t.get("users"), list) and len(t.get("users")) > 0 else "Unassigned")
        context.append(f"- [{t.get('status')}] {t.get('title')} (Priority: {t.get('priority')}) - Assigned to: {dev_name}")
        
    context.append("\nRECENT ACTIVITY:")
    for a in activities:
        user_name = a.get("users", {}).get("name") if isinstance(a.get("users"), dict) else (a.get("users")[0].get("name") if isinstance(a.get("users"), list) and len(a.get("users")) > 0 else "Unknown")
        context.append(f"- {user_name} ({a.get('action')}): {a.get('description')} at {a.get('created_at')}")
        
    if github_data:
        context.append(f"\nGITHUB REPOSITORY: {github_data['repository']}")
        context.append("Recent Commits:")
        for c in github_data['commits']:
            context.append(f" - {c['author']}: {c['message']} ({c['date']})")
        context.append("Recent PRs:")
        for pr in github_data['pull_requests']:
            context.append(f" - [{pr['state']}] {pr['title']} by {pr['author']}")
        context.append("Recent Issues:")
        for i in github_data['issues']:
            context.append(f" - [{i['state']}] {i['title']}")
            
    return "\n".join(context)


async def generate_continuity_summary_service(project_id: str, current_user: UserOut) -> ContinuitySummaryOut:
    context_str = await _build_project_context(project_id)
    
    prompt = f"""
    You are an AI assistant for the 'AI DevFlow Intelligence Suite'.
    Your task is to generate a comprehensive AI Continuity / Handover Summary for a developer joining an existing project.
    
    VERY IMPORTANT RULES:
    1. Use ONLY the supplied project context.
    2. Do NOT invent tasks, developers, commits, PRs, issues, or blockers.
    3. Do NOT falsely claim a GitHub commit belongs to a specific DevFlow task unless a relationship is obvious (like a task ID in a commit message).
    4. If information is unavailable, state it clearly (e.g., "No GitHub data available").
    
    PROJECT CONTEXT:
    {context_str}
    
    Please provide the output as a valid JSON object matching this schema precisely, with string values for each key:
    {{
        "project_overview": "What the project is, current status, progress, PM.",
        "previous_developer_work": "What previous developers completed or worked on based on activity and tasks.",
        "current_work": "Tasks currently in progress or review, and current developers working.",
        "pending_work": "Tasks that are TODO or pending.",
        "blocked_overdue_work": "Only if there are overdue tasks (due date past) or known blockers.",
        "recent_github_activity": "Summary of recent commits, PRs, and issues.",
        "known_issues": "Verified open issues or project problems.",
        "important_context": "Important context for the new developer.",
        "what_next_developer_should_know": "Concise practical handover information.",
        "recommended_next_steps": "Next actions based ONLY on existing open tasks, PRs, or issues. No smart recommendations."
    }}
    """
    
    llm_response = await ask_llm(prompt, json_response=True)
    
    try:
        # Sometimes LLMs wrap JSON in markdown block
        clean_json = llm_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        
        parsed = json.loads(clean_json)
        
        # Log activity
        log_activity(
            user_id=current_user.id,
            action="AI_CONTINUITY_GENERATED",
            entity_type="project",
            entity_id=project_id,
            description="Generated AI Continuity summary"
        )
        
        # We can insert to a continuity table here if needed. But we'll just return it.
        return ContinuitySummaryOut(**parsed)
    except Exception as e:
        print(f"Failed to parse JSON from AI: {llm_response}")
        raise HTTPException(status_code=500, detail="AI provided an invalid summary format.")

async def ask_continuity_question_service(project_id: str, question: str, current_user: UserOut) -> str:
    context_str = await _build_project_context(project_id)
    
    prompt = f"""
    You are an AI assistant for the 'AI DevFlow Intelligence Suite'.
    The user is asking a question about a project to get continuity/handover information.
    
    VERY IMPORTANT RULES:
    1. Use ONLY the supplied project context.
    2. Do NOT invent information.
    3. If the answer is not in the context, say "The available project data is insufficient to answer this question."
    
    PROJECT CONTEXT:
    {context_str}
    
    QUESTION:
    {question}
    """
    
    llm_response = await ask_llm(prompt, json_response=False)
    
    log_activity(
        user_id=current_user.id,
        action="AI_CONTINUITY_QUESTION_ASKED",
        entity_type="project",
        entity_id=project_id,
        description=f"Asked AI continuity question: {question[:50]}..."
    )
    
    return llm_response.strip()
