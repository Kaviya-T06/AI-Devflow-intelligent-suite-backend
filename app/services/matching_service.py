"""
Developer matching and ranking engine.
"""
from typing import List, Dict, Any
from app.db.supabase_client import get_supabase_client

# Weights
WEIGHT_SKILL = 0.50
WEIGHT_EXPERIENCE = 0.20
WEIGHT_WORKLOAD = 0.20
WEIGHT_HISTORY = 0.10


def calculate_match_score(developer: dict, task: dict) -> dict:
    """Deterministic ranking function."""
    raw_required_skills = task.get("required_skills") or []
    required_skills = []
    for s in raw_required_skills:
        if isinstance(s, dict):
            required_skills.append(s.get("name", "").lower().strip())
        elif isinstance(s, str):
            required_skills.append(s.lower().strip())

    dev_skills = {
        s.get("name", "").lower().strip(): s.get("proficiency", 1)
        for s in (developer.get("skills") or [])
    }
    
    # 1. Skill Match (50%)
    skill_score = 0
    if not required_skills:
        skill_score = 1.0  # Free match if no skills required
    else:
        matched = 0
        total_proficiency = 0
        for skill in required_skills:
            if skill in dev_skills:
                matched += 1
                total_proficiency += min(dev_skills[skill], 5) / 5.0
        
        # Calculate ratio of matched skills, adjusted by proficiency
        if matched > 0:
            skill_score = (matched / len(required_skills)) * 0.7 + (total_proficiency / len(required_skills)) * 0.3
    
    # 2. Experience Match (20%)
    exp_score = 0
    req_exp = task.get("min_experience_years") or 0
    dev_exp = developer.get("experience_years") or 0
    
    if req_exp == 0:
        exp_score = 1.0
    elif dev_exp >= req_exp:
        exp_score = 1.0
    else:
        exp_score = dev_exp / req_exp

    # 3. Workload Match (20%)
    capacity = developer.get("capacity_hours_per_week") or 40
    # In a real app we'd fetch active tasks. Here we assume we fetch assigned tasks count
    active_tasks = developer.get("active_task_count", 0)
    # Simple heuristic: 1 task ~ 10 hours
    utilization = (active_tasks * 10) / max(capacity, 1)
    
    if utilization > 1.0:
        workload_score = 0.0
    else:
        workload_score = 1.0 - utilization

    # 4. History Match (10%)
    history_score = 0.5 # Default middle ground without complex history linking
    
    # Final Score
    total_score = (
        (skill_score * WEIGHT_SKILL) +
        (exp_score * WEIGHT_EXPERIENCE) +
        (workload_score * WEIGHT_WORKLOAD) +
        (history_score * WEIGHT_HISTORY)
    )

    return {
        "score": round(total_score * 100),
        "skill_match": skill_score,
        "missing_skills": [s for s in required_skills if s not in dev_skills],
        "workload_score": workload_score,
        "task_required_skills": required_skills,
    }


def get_recommended_team_for_task(task_id: str) -> List[dict]:
    """Fetch task, get developers, return ranked candidates."""
    db = get_supabase_client()
    
    # Fetch Task
    task_resp = db.table("tasks").select("*").eq("id", task_id).limit(1).execute()
    if not task_resp.data:
        raise ValueError("Task not found")
    task = task_resp.data[0]
    
    # Fetch Developers (profiles where role = DEVELOPER)
    # Since profiles table tracks role:
    devs_resp = db.table("profiles").select("*").eq("role", "DEVELOPER").execute()
    developers = devs_resp.data or []
    
    # Pre-fetch active tasks for workload calculation
    tasks_resp = db.table("tasks").select("assigned_to, status").in_("status", ["TODO", "IN_PROGRESS", "REVIEW"]).execute()
    active_tasks = tasks_resp.data or []
    task_counts = {}
    for t in active_tasks:
        assignee = t.get("assigned_to")
        if assignee:
            task_counts[assignee] = task_counts.get(assignee, 0) + 1
            
    candidates = []
    for dev in developers:
        dev["active_task_count"] = task_counts.get(dev["id"], 0)
        match_info = calculate_match_score(dev, task)
        
        candidates.append({
            "developer": dev,
            "match_score": match_info["score"],
            "missing_skills": match_info["missing_skills"],
            "task_required_skills": match_info["task_required_skills"],
            "basic_explanation": f"Match Score: {match_info['score']}%. Skill match: {match_info['skill_match']:.2f}. Workload score: {match_info['workload_score']:.2f}."
        })
        
    # Rank descending
    candidates.sort(key=lambda x: x["match_score"], reverse=True)
    return candidates

async def get_explained_recommendations(task_id: str) -> List[dict]:
    """Fetch candidates and ask Gemini for explanation if available."""
    candidates = get_recommended_team_for_task(task_id)
    
    if not candidates:
        return []
        
    try:
        from app.services.ai_service import ask_llm
        prompt = "Explain these developer recommendations for a task based on their scores, skill matches, and workloads:\n"
        for i, c in enumerate(candidates):
            dev = c['developer']
            prompt += f"Candidate {i+1}: {dev.get('full_name')} - Score {c['match_score']}%. "
            prompt += f"Missing skills: {', '.join(c['missing_skills']) if c['missing_skills'] else 'None'}. "
            prompt += f"Workload score: {c['workload_score']}. "
            prompt += f"Active tasks: {dev.get('active_task_count')}.\n"
        
        prompt += "\nProvide a short, clear explanation for why each is a good fit and any concerns."
        
        explanation = await ask_llm(prompt)
        
        # We can either attach the full explanation to the first result or distribute it.
        # Here we just attach it to the list as a whole or to each candidate's ai_explanation
        for c in candidates:
            c["ai_explanation"] = explanation # In a real app we might parse or use JSON
            
    except Exception as e:
        # Fallback to deterministic basic explanation
        print("Gemini fallback:", e)
        for c in candidates:
            c["ai_explanation"] = c["basic_explanation"]
            
    return candidates
