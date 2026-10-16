import requests
import json

base_url = 'http://127.0.0.1:8000/api/v1'

# 1. Login with real PM credentials
print("--- STEP 1: AUTHENTICATION ---")
login_res = requests.post(f'{base_url}/auth/login', json={
    'email': 'rahul.kumar@gmail.com',
    'password': 'Rahul@123'
})
assert login_res.status_code == 200, f'Login failed: {login_res.text}'
login_data = login_res.json()
token = login_data['access_token']
headers = {'Authorization': f'Bearer {token}'}
print(f"Logged in successfully as {login_data['user']['name']} ({login_data['user']['email']}), Role: {login_data['user']['role']}")

# 2. PM Dashboard Stats (PMProjectPulsePage mount)
print("\n--- STEP 2: PROJECT PULSE DATA LOAD ---")
stats_res = requests.get(f'{base_url}/dashboard/pm-stats', headers=headers)
assert stats_res.status_code == 200, f"Stats failed: {stats_res.text}"
stats = stats_res.json()
projects = stats.get('projects', [])
print(f"Fetched {len(projects)} PM-managed projects.")
for p in projects:
    print(f"  - [{p['id']}] {p['name']} | Health: {p.get('health_score')}/100 ({p.get('health_status')}) | Tasks: {p.get('total_tasks')} | Overdue: {p.get('overdue_tasks')}")

p1 = projects[0]
p1_id = p1['id']
p1_name = p1['name']
print(f"\nActive Project 1 Selected: '{p1_name}' ({p1_id})")
print(f"  Health Score: {p1.get('health_score')}")
print(f"  Health Status: {p1.get('health_status')}")
print(f"  Component Scores: {p1.get('component_scores')}")
print(f"  Task Status Distribution: {p1.get('task_status_distribution')}")

# 3. Risks for Project 1
risks_res = requests.get(f'{base_url}/workflow-risks?project_id={p1_id}', headers=headers)
assert risks_res.status_code == 200
p1_risks = risks_res.json()
print(f"  Active Workflow Risks: {len(p1_risks)} risks detected")
for r in p1_risks[:2]:
    print(f"    * [{r.get('severity')}] {r.get('risk_type')}: {r.get('description')[:60]}...")

# 4. Activity Feed for Project 1
act_res = requests.get(f'{base_url}/dashboard/pm-activity?limit=10&project_id={p1_id}', headers=headers)
assert act_res.status_code == 200
p1_act = act_res.json()
print(f"  Activity Feed items: {len(p1_act)}")

# 5. Executive Briefing for Project 1
print("\n--- STEP 3: AI EXECUTIVE BRIEFING ---")
b1_res = requests.get(f'{base_url}/dashboard/projects/{p1_id}/pulse-briefing', headers=headers)
assert b1_res.status_code == 200, f"Briefing failed: {b1_res.text}"
b1 = b1_res.json()
print(f"Headline: {b1.get('executive_summary_headline')}")
print(f"Schedule Status: {b1.get('schedule_status')}")
print(f"Top 3 Risks: {len(b1.get('top_3_critical_risks', []))} items")
print(f"Data Freshness: {b1.get('data_freshness')}")
print(f"Is Simulation / Fallback: {b1.get('is_fallback', False)}")
if b1.get('blockers'):
    print(f"Active Blockers: {b1.get('blockers')[:1]}")

# 6. Recovery Recommendations for Project 1
print("\n--- STEP 4: AI RECOVERY RECOMMENDATIONS ---")
r1_res = requests.get(f'{base_url}/dashboard/projects/{p1_id}/pulse-recommendations', headers=headers)
assert r1_res.status_code == 200, f"Recommendations failed: {r1_res.text}"
r1 = r1_res.json()
recs = r1.get('recommendations', [])
print(f"Recommendations count: {len(recs)}")
for idx, rec in enumerate(recs[:3]):
    print(f"  Rec #{idx+1}: [{rec.get('priority')}] {rec.get('action_title')}")
    print(f"         Type: {rec.get('action_type')} | Effort: {rec.get('estimated_effort_hours')}h | Impact: {rec.get('impact_metric')}")
    print(f"         Rationale: {rec.get('rationale')[:80]}...")

# 7. Project Switching Test (Verifying no context contamination)
print("\n--- STEP 5: PROJECT SWITCHING & CONTEXT ISOLATION ---")
if len(projects) > 1:
    p2 = projects[1]
    p2_id = p2['id']
    p2_name = p2['name']
    print(f"Switching project to: '{p2_name}' ({p2_id})")
    
    b2_res = requests.get(f'{base_url}/dashboard/projects/{p2_id}/pulse-briefing', headers=headers)
    assert b2_res.status_code == 200
    b2 = b2_res.json()
    print(f"Project 2 Headline: {b2.get('executive_summary_headline')}")
    print(f"Project 2 Schedule: {b2.get('schedule_status')}")
    assert b2.get('project_id') == p2_id
    print("Project context successfully swapped to Project 2 (No stale project ID leakage).")

# 8. Access Control & Validation
print("\n--- STEP 6: ACCESS CONTROL & ERROR HANDLING ---")
# Non-existent project
non_existent = requests.get(f'{base_url}/dashboard/projects/00000000-0000-0000-0000-000000000000/pulse-briefing', headers=headers)
print(f"Non-existent UUID -> HTTP {non_existent.status_code} (Expected 404)")
assert non_existent.status_code == 404

# Malformed UUID string
malformed = requests.get(f'{base_url}/dashboard/projects/not-a-valid-uuid/pulse-briefing', headers=headers)
print(f"Malformed UUID -> HTTP {malformed.status_code} (Expected 404)")
assert malformed.status_code == 404

# Missing Authorization header
no_auth = requests.get(f'{base_url}/dashboard/projects/{p1_id}/pulse-briefing')
print(f"Missing Token -> HTTP {no_auth.status_code} (Expected 401)")
assert no_auth.status_code == 401

# Invalid Token
bad_auth = requests.get(f'{base_url}/dashboard/projects/{p1_id}/pulse-briefing', headers={'Authorization': 'Bearer invalid.token.value'})
print(f"Invalid Token -> HTTP {bad_auth.status_code} (Expected 401)")
assert bad_auth.status_code == 401

print("\n>>> ALL LIVE PM PROJECT PULSE VERIFICATION CHECKS PASSED SUCCESSFULLY! <<<")
