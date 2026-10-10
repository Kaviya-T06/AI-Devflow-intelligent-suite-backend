import asyncio
from app.services.matching_service import calculate_match_score

async def run_test():
    print("--- Phase 3: Setting up Test Data ---")
    
    # 1. Developer A (Expert Python, 5 yrs)
    dev_a = {
        "id": "1",
        "full_name": "Developer A",
        "email": "deva@test.com",
        "role": "DEVELOPER",
        "skills": [
            {"name": "Python", "proficiency": 5, "level": "Expert"},
            {"name": "React", "proficiency": 3, "level": "Intermediate"}
        ],
        "experience_years": 5,
        "capacity_hours_per_week": 40,
        "active_task_count": 0
    }
    
    # 2. Developer B (Beginner Python, 1 yr)
    dev_b = {
        "id": "2",
        "full_name": "Developer B",
        "email": "devb@test.com",
        "role": "DEVELOPER",
        "skills": [
            {"name": "Python", "proficiency": 1, "level": "Beginner"}
        ],
        "experience_years": 1,
        "capacity_hours_per_week": 40,
        "active_task_count": 0
    }
    
    print("Created Developer A and B.")
    
    # 3. Test Task (Requires Expert Python)
    task = {
        "id": "3",
        "title": "Build AI Matching Engine",
        "description": "Needs advanced Python skills.",
        "status": "TODO",
        "required_skills": ["Python"],
        "min_experience_years": 3,
    }
    print("Created Test Task.\n")
    
    print("--- Getting Smart Allocation Recommendations ---")
    
    candidates = []
    for dev in [dev_a, dev_b]:
        match_info = calculate_match_score(dev, task)
        candidates.append({
            "developer": dev,
            "match_score": match_info["score"],
            "missing_skills": match_info["missing_skills"],
            "basic_explanation": f"Match Score: {match_info['score']}%. Skill match: {match_info['skill_match']:.2f}. Workload score: {match_info['workload_score']:.2f}."
        })
        
    # Rank descending
    candidates.sort(key=lambda x: x["match_score"], reverse=True)
    
    for rec in candidates:
        dev = rec['developer']
        score = rec['match_score']
        print(f"Developer: {dev['full_name']}")
        print(f"Match Score: {score}%")
        print(f"Explanation: {rec['basic_explanation']}\n")

if __name__ == "__main__":
    asyncio.run(run_test())
