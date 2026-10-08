"""
Minimal standalone test for the AI service.
Run from the backend directory:

    python test_ai_service.py

This tests the provider connection BEFORE using the full FastAPI stack.
"""
import asyncio
import sys
import os

# Add the project root to sys.path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings
from app.services.ai_service import ask_llm


async def main():
    print("=" * 60)
    print("AI Service Direct Test")
    print("=" * 60)
    print(f"Provider : {settings.AI_PROVIDER}")
    print(f"Model    : {settings.AI_MODEL}")
    key = settings.AI_API_KEY
    if key:
        print(f"API Key  : {key[:4]}...{key[-4:]} (length={len(key)})")
    else:
        print("API Key  : *** NOT SET ***")
    print()

    test_prompt = """Generate a short summary of this project:

Project: Employee Management Portal
Status: Active

Completed:
- Backend API development

In Progress:
- Database schema
- Frontend functionality

Pending:
- Testing
"""

    print("Sending test prompt to AI provider...")
    print("-" * 60)
    try:
        result = await ask_llm(test_prompt, json_response=False)
        print("SUCCESS — AI provider returned a response:")
        print()
        print(result)
    except Exception as e:
        print(f"FAILED — {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
