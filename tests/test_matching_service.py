import pytest
from app.services.matching_service import calculate_match_score

def test_matching_no_required_skills():
    dev = {"skills": [{"name": "Python", "proficiency": 5}], "experience_years": 2, "capacity_hours_per_week": 40}
    task = {"required_skills": [], "min_experience_years": 0}
    result = calculate_match_score(dev, task)
    assert result["skill_match"] == 1.0
    assert result["score"] > 80

def test_matching_skill_normalization():
    dev = {"skills": [{"name": "React", "proficiency": 4}]}
    task = {"required_skills": ["react"]}
    result = calculate_match_score(dev, task)
    assert result["skill_match"] > 0
    assert len(result["missing_skills"]) == 0

def test_matching_missing_skills():
    dev = {"skills": [{"name": "React", "proficiency": 4}]}
    task = {"required_skills": ["react", "node"]}
    result = calculate_match_score(dev, task)
    assert "node" in result["missing_skills"]
    assert result["skill_match"] < 1.0

def test_matching_proficiency_and_experience():
    dev1 = {"skills": [{"name": "Go", "proficiency": 2}], "experience_years": 1}
    dev2 = {"skills": [{"name": "Go", "proficiency": 5}], "experience_years": 5}
    task = {"required_skills": ["go"], "min_experience_years": 3}
    
    res1 = calculate_match_score(dev1, task)
    res2 = calculate_match_score(dev2, task)
    assert res2["score"] > res1["score"]

def test_workload_handling():
    dev1 = {"skills": [], "active_task_count": 0, "capacity_hours_per_week": 40}
    dev2 = {"skills": [], "active_task_count": 10, "capacity_hours_per_week": 40} # Overloaded
    task = {"required_skills": []}
    
    res1 = calculate_match_score(dev1, task)
    res2 = calculate_match_score(dev2, task)
    
    assert res1["workload_score"] > res2["workload_score"]
    assert res2["workload_score"] == 0.0

def test_missing_profile_information():
    dev_empty = {}
    task = {"required_skills": ["python"], "min_experience_years": 2}
    result = calculate_match_score(dev_empty, task)
    assert result["skill_match"] == 0.0
    assert result["score"] < 50
