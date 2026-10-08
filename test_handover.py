import asyncio, sys, os
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.getcwd())

from app.services.continuity_service import generate_continuity_summary_service
from app.db.supabase_client import get_supabase_client
from app.schemas.user import UserOut

async def main():
    try:
        # Get a real project ID from db
        db = get_supabase_client()
        proj_resp = db.table("projects").select("id").limit(1).execute()
        project_id = proj_resp.data[0]["id"]
        
        user = UserOut(id="123", email="test@test.com", name="Test User", role="admin", is_active=True, created_at="2026-01-01T00:00:00Z")
        
        print("Testing generate_continuity_summary_service...")
        result = await generate_continuity_summary_service(project_id, user)
        print("SUCCESS! Project Overview:", result.project_overview[:50])
    except Exception as e:
        print("FAILED:", str(e))

asyncio.run(main())
