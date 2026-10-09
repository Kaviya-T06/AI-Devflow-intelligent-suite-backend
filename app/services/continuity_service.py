import json
import re
from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from app.db.supabase_client import get_supabase_client
from app.schemas.user import UserOut
from app.schemas.continuity import ContinuitySummaryOut, ChatMessage
from app.services.ai_service import ask_llm
from app.services.activity_service import log_activity
from app.api.v1.endpoints.github import fetch_github_api
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
        
        try:
            commits = await fetch_github_api(f"https://api.github.com/repos/{full_name}/commits?per_page=5")
        except Exception:
            commits = []
            
        try:
            prs = await fetch_github_api(f"https://api.github.com/repos/{full_name}/pulls?state=all&per_page=5")
        except Exception:
            prs = []
            
        try:
            issues_res = await fetch_github_api(f"https://api.github.com/repos/{full_name}/issues?state=all&per_page=5")
            issues = [i for i in issues_res if "pull_request" not in i]
        except Exception:
            issues = []

        branches = []
        branches_error = None
        try:
            branches_raw = await fetch_github_api(f"https://api.github.com/repos/{full_name}/branches?per_page=30")
            branches = [{"name": b["name"], "sha": b["commit"]["sha"]} for b in branches_raw if isinstance(b, dict) and "name" in b and isinstance(b.get("commit"), dict)]
        except Exception as branch_exc:
            branches_error = str(getattr(branch_exc, "detail", branch_exc))

        result = {
            "repository": full_name,
            "commits": [{"sha": c.get("sha", ""), "message": c["commit"]["message"], "author": c["commit"]["author"]["name"], "date": c["commit"]["author"]["date"]} for c in commits if isinstance(c, dict) and "commit" in c],
            "pull_requests": [{"title": p["title"], "body": p.get("body", ""), "state": p["state"], "author": p["user"]["login"]} for p in prs if isinstance(p, dict) and "title" in p],
            "issues": [{"title": i["title"], "state": i["state"]} for i in issues if isinstance(i, dict) and "title" in i],
            "branches": branches,
        }
        if branches_error is not None:
            result["branches_error"] = branches_error
        return result
    except Exception:
        return None

async def _build_project_context(project_id: str) -> tuple[str, dict]:
    # 1. Fetch Project
    proj_resp = _db().table("projects").select("*, users(name)").eq("id", project_id).execute()
    if not proj_resp.data:
        raise HTTPException(status_code=404, detail="Project not found")
    project = proj_resp.data[0]
    
    # 2. Fetch Tasks
    tasks_resp = _db().table("tasks").select("*, users(name)").eq("project_id", project_id).execute()
    tasks = tasks_resp.data or []
    
    # 3. Fetch Activity (Both Project-Level and Task-Level activity for this project)
    try:
        activity_resp = _db().table("activity_logs").select("*, users(name)").eq("project_id", project_id).order("created_at", desc=True).limit(50).execute()
        activities_by_project = activity_resp.data or []
    except Exception:
        activities_by_project = []

    # Also check entity_id == project_id for legacy records where project_id may have been unset
    try:
        legacy_resp = _db().table("activity_logs").select("*, users(name)").eq("entity_id", project_id).order("created_at", desc=True).limit(50).execute()
        legacy_activities = legacy_resp.data or []
    except Exception:
        legacy_activities = []

    # Combine and de-duplicate by log ID
    project_task_ids = {str(t["id"]).lower() for t in tasks if t.get("id")}
    seen_log_ids = set()
    activities = []
    
    for a in activities_by_project + legacy_activities:
        log_id = str(a.get("id") or "")
        if log_id and log_id in seen_log_ids:
            continue
        if log_id:
            seen_log_ids.add(log_id)
            
        # Security / Integrity verification: Ensure task activity strictly belongs to this project
        e_type = str(a.get("entity_type") or "").lower()
        e_id = str(a.get("entity_id") or "").lower()
        p_id = str(a.get("project_id") or "").lower()
        
        # If project_id matches, it belongs to this project.
        # If project_id is missing/legacy, verify entity_id is the project itself or in this project's tasks
        if p_id == str(project_id).lower() or e_id == str(project_id).lower() or (e_type == "task" and e_id in project_task_ids):
            activities.append(a)

    # Preserve strict chronological ordering (newest first for recent activity display) and bound limit to 30
    activities.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    activities = activities[:30]
    
    # 4. Fetch GitHub
    github_data = await _fetch_github_data(project_id)

    # 5. Fetch Workflow Risks directly from workflow_risks table
    try:
        risks_resp = _db().table("workflow_risks").select("*").eq("project_id", project_id).execute()
        all_risks = risks_resp.data or []
    except Exception:
        all_risks = []

    risks = all_risks
    open_risks = [r for r in all_risks if not r.get("is_resolved") and (r.get("status") or "").upper() != "RESOLVED"]
    resolved_risks = [r for r in all_risks if r.get("is_resolved") or (r.get("status") or "").upper() == "RESOLVED"]

    severity_prio = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    def risk_sort_key(r):
        lvl = str(r.get("level") or "MEDIUM").upper()
        prio = severity_prio.get(lvl, 4)
        det = str(r.get("detected_at") or r.get("created_at") or "")
        return (prio, det)

    try:
        open_risks.sort(key=risk_sort_key)
    except Exception:
        pass

    resolved_risks = sorted(resolved_risks, key=lambda r: str(r.get("resolved_at") or r.get("updated_at") or r.get("created_at") or ""), reverse=True)[:10]

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
        
    verified_mappings = []
    unmapped_commits = []
    unmapped_prs = []
    
    if github_data:
        context.append(f"\nGITHUB REPOSITORY: {github_data['repository']}")
        
        valid_task_ids = {str(t["id"]).lower(): t for t in tasks if t.get("id")}
        uuid_pattern = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE)
        
        for c in github_data['commits']:
            msg = c['message']
            found_uuids = uuid_pattern.findall(msg)
            mapped = False
            for uid in found_uuids:
                uid_lower = uid.lower()
                if uid_lower in valid_task_ids:
                    t = valid_task_ids[uid_lower]
                    sha = c.get('sha', '')[:7]
                    verified_mappings.append(f"- Commit {sha} -> DevFlow Task: {uid_lower} | Task Title: {t.get('title')} | Mapping Source: commit message | Verified: true\n  Commit Message: {msg}")
                    mapped = True
                    break
            if not mapped:
                unmapped_commits.append(c)

        for pr in github_data['pull_requests']:
            text = f"{pr['title']} {pr.get('body', '')}"
            found_uuids = uuid_pattern.findall(text)
            mapped = False
            for uid in found_uuids:
                uid_lower = uid.lower()
                if uid_lower in valid_task_ids:
                    t = valid_task_ids[uid_lower]
                    verified_mappings.append(f"- Pull Request [{pr['state']}] '{pr['title']}' -> DevFlow Task: {uid_lower} | Task Title: {t.get('title')} | Mapping Source: PR title/body | Verified: true")
                    mapped = True
                    break
            if not mapped:
                unmapped_prs.append(pr)
                
        context.append("\nVERIFIED GITHUB TASK MAPPINGS:")
        if verified_mappings:
            context.extend(verified_mappings)
        else:
            context.append("- None")
            
        context.append("\nUNMAPPED GITHUB ACTIVITY (Project-level only, do NOT assign to tasks):")
        context.append("Unmapped Commits:")
        for c in unmapped_commits:
            context.append(f" - {c['author']}: {c['message']} ({c['date']})")
        context.append("Unmapped PRs:")
        for pr in unmapped_prs:
            context.append(f" - [{pr['state']}] {pr['title']} by {pr['author']}")
        context.append("Issues:")
        for i in github_data['issues']:
            context.append(f" - [{i['state']}] {i['title']}")

        context.append("\nGITHUB BRANCHES (do NOT infer task ownership, task status, or completion from branch names):")
        if github_data.get("branches_error"):
            context.append(f"- Branch data unavailable: {github_data['branches_error']}")
        elif github_data.get("branches"):
            for b in github_data["branches"]:
                context.append(f" - {b['name']} (latest commit: {b['sha'][:7]})")
        else:
            context.append("- No branches found.")
    context.append("\nVERIFIED WORKFLOW RISKS:")
    context.append("Open Risks:")
    if open_risks:
        for r in open_risks:
            r_id = r.get("id", "")
            r_type = r.get("risk_type", "UNKNOWN")
            r_level = r.get("level", "Medium")
            r_status = r.get("status", "OPEN")
            r_title = r.get("title", "")
            r_detected = r.get("detected_at") or r.get("created_at") or "Unknown"
            r_desc = r.get("description", "")
            r_task = r.get("task_id")
            task_info = f" | Task ID: {r_task}" if r_task else ""
            title_info = f" - Title: {r_title}" if r_title else ""
            context.append(f"- [Risk ID: {r_id}] Type: {r_type} | Severity: {r_level} | Status: {r_status} | Detected: {r_detected}{task_info}{title_info}\n  Description: {r_desc}")
    else:
        context.append("- None: No active open workflow risks detected for this project.")

    context.append("\nResolved Risks:")
    if resolved_risks:
        resolved_sorted = sorted(resolved_risks, key=lambda r: str(r.get("resolved_at") or r.get("updated_at") or ""), reverse=True)[:10]
        for r in resolved_sorted:
            r_id = r.get("id", "")
            r_type = r.get("risk_type", "UNKNOWN")
            r_level = r.get("level", "Medium")
            r_status = r.get("status", "RESOLVED")
            r_title = r.get("title", "")
            r_detected = r.get("detected_at") or r.get("created_at") or "Unknown"
            r_resolved = r.get("resolved_at") or r.get("updated_at") or "Unknown"
            r_desc = r.get("description", "")
            r_task = r.get("task_id")
            task_info = f" | Task ID: {r_task}" if r_task else ""
            title_info = f" - Title: {r_title}" if r_title else ""
            context.append(f"- [Risk ID: {r_id}] Type: {r_type} | Severity: {r_level} | Status: {r_status} | Detected: {r_detected} | Resolved: {r_resolved}{task_info}{title_info}\n  Description: {r_desc}")
    else:
        context.append("- None")

    raw_dict = {
        "project": project,
        "tasks": tasks,
        "activities": activities,
        "risks": risks,
        "github": github_data or {"repository": None, "commits": [], "pull_requests": [], "issues": [], "branches": []},
        "verified_mappings": verified_mappings,
        "unmapped_commits": unmapped_commits,
        "unmapped_prs": unmapped_prs
    }
    return "\n".join(context), raw_dict

async def generate_continuity_summary_service(project_id: str, current_user: UserOut) -> ContinuitySummaryOut:
    context_str, raw_dict = await _build_project_context(project_id)
    
    prompt = f"""
    You are an AI assistant for the 'AI DevFlow Intelligence Suite'.
    Your task is to generate a comprehensive AI Continuity / Handover Summary for a developer joining an existing project.
    
    VERY IMPORTANT RULES:
    1. Use ONLY the supplied project context.
    2. Do NOT invent tasks, developers, commits, PRs, issues, or blockers.
    3. Do NOT falsely claim a GitHub commit belongs to a specific DevFlow task unless a relationship is obvious (like a task ID in a commit message).
    4. If information is unavailable, state it clearly (e.g., "No GitHub data available").
    5. Rely strictly on the VERIFIED WORKFLOW RISKS section for identifying known risks, blockers, and overdue work. Do NOT invent or infer risks not present in that section. If there are no open risks, state that explicitly.
    
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
    
    try:
        llm_response = await ask_llm(prompt, json_response=True)
        
        # Sometimes LLMs wrap JSON in markdown block
        clean_json = llm_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.startswith("```"):
            clean_json = clean_json[3:]
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
        
        parsed["raw_context"] = raw_dict
        return ContinuitySummaryOut(**parsed)

    except Exception as e:
        # Determine error detail — HTTPException.detail is the structured message
        from fastapi import HTTPException as _HTTPException
        if isinstance(e, _HTTPException):
            error_detail = str(e.detail)
            http_code = e.status_code
        else:
            error_detail = str(e)
            http_code = 502

        print(
            f"[AI Handover] FAILED for project_id={project_id} | "
            f"HTTPStatus={http_code} | Detail={error_detail}"
        )
        
        raise _HTTPException(
            status_code=http_code,
            detail={
                "message": str(error_detail),
                "raw_context": raw_dict
            }
        )

async def ask_continuity_question_service(
    project_id: str, 
    question: str, 
    current_user: UserOut,
    messages: Optional[list] = None
) -> str:
    context_str, _ = await _build_project_context(project_id)
    
    # Bounded short-term conversation context (max 10 recent messages)
    MAX_HISTORY = 10
    recent_messages = (messages or [])[-MAX_HISTORY:]
    
    conversation_formatted = []
    history_items = list(recent_messages)
    if history_items:
        last_item = history_items[-1]
        last_role = getattr(last_item, "role", None) if not isinstance(last_item, dict) else last_item.get("role")
        last_content = getattr(last_item, "content", None) if not isinstance(last_item, dict) else last_item.get("content")
        if (last_role or "").lower() == "user" and (last_content or "").strip() == question.strip():
            history_items = history_items[:-1]
            
    for m in history_items:
        role = getattr(m, "role", None) if not isinstance(m, dict) else m.get("role")
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
        role_label = "User" if (role or "").lower() == "user" else "Assistant"
        c = (content or "").strip()
        if c:
            conversation_formatted.append(f"{role_label}: {c}")
            
    if conversation_formatted:
        recent_conv_str = "\nRECENT CONVERSATION:\n" + "\n".join(conversation_formatted)
    else:
        recent_conv_str = "\nRECENT CONVERSATION:\nNone"
        
    prompt = f"""
    You are an AI assistant for the 'AI DevFlow Intelligence Suite'.
    The user is asking a question about a project to get continuity/handover information.
    
    VERY IMPORTANT RULES:
    1. Use ONLY the supplied verified project context as the primary source of truth.
    2. Do NOT invent information.
    3. Base answers regarding risks, blockers, and bottlenecks strictly on the VERIFIED WORKFLOW RISKS section.
    4. Conversation history is contextual only and must not be treated as verified project data. Use the verified project context as the source of truth. If the verified project context does not contain enough information to answer, state that you do not have enough verified project information.
    5. If the answer is not in the context, say "The available project data is insufficient to answer this question."
    
    PROJECT CONTEXT:
    {context_str}
    {recent_conv_str}
    
    QUESTION:
    {question}
    """
    
    try:
        llm_response = await ask_llm(prompt, json_response=False)
        
        log_activity(
            user_id=current_user.id,
            action="AI_CONTINUITY_QUESTION_ASKED",
            entity_type="project",
            entity_id=project_id,
            description=f"Asked AI continuity question: {question[:50]}..."
        )
        
        return llm_response.strip()
    except Exception as e:
        from fastapi import HTTPException as _HTTPException
        if isinstance(e, _HTTPException):
            raise e
        raise _HTTPException(status_code=502, detail=str(e))
