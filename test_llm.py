import asyncio, sys, os
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, os.getcwd())
from app.services.ai_service import ask_llm
from app.core.config import settings
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    print(f'AI provider: {settings.AI_PROVIDER}')
    print(f'AI model: {settings.AI_MODEL}')
    print(f'AI API key configured: {bool(settings.AI_API_KEY)}')
    
    print('[AI HANDOVER] Request started')
    try:
        res = await ask_llm('Reply with exactly: AI_TEST_OK', json_response=True)
        print('[AI HANDOVER] Provider response received')
        print('SUCCESS:', res)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
