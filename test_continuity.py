import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db.supabase_client import get_supabase_client
from app.services.continuity_service import generate_continuity_summary_service

# Mock user for testing
class MockUser:
    def __init__(self, user_id):
        self.id = user_id

async def main():
    db = get_supabase_client()
    # Get a real project
    res = db.table("projects").select("*").limit(1).execute()
    if not res.data:
        print("No projects found.")
        return
    
    project = res.data[0]
    project_id = project["id"]
    
    # Get a real user associated with the project (or any user)
    user_res = db.table("users").select("*").limit(1).execute()
    if not user_res.data:
        print("No users found.")
        return
    user_id = user_res.data[0]["id"]
    
    print(f"Testing continuity for project: {project['name']} ({project_id})")
    
    try:
        summary = await generate_continuity_summary_service(project_id, MockUser(user_id))
        print("\n--- SUMMARY ---")
        print("Overview:", summary.project_overview)
        print("Known Issues:", summary.known_issues)
        print("Important Context:", summary.important_context)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
