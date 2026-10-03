import json
import httpx
from fastapi import HTTPException
from app.core.config import settings

async def ask_llm(prompt: str, json_response: bool = False) -> str:
    """
    Sends a prompt to the AI provider (Gemini API) and returns the generated text.
    If json_response is True, requests JSON output structure.
    """
    api_key = settings.AI_API_KEY.strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="AI_API_KEY is not configured.")

    # Using Gemini Flash Latest as standard fast model
    model = "gemini-3.8-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    if json_response:
        payload["generationConfig"] = {
            "responseMimeType": "application/json"
        }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            if "candidates" in data and len(data["candidates"]) > 0:
                parts = data["candidates"][0]["content"]["parts"]
                if parts:
                    return parts[0]["text"]
            return ""
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {e.response.text}"
        safe_msg = error_msg.replace(api_key, "***")
        raise HTTPException(status_code=502, detail=f"Failed to communicate with AI provider: {safe_msg}")
    except Exception as e:
        safe_error = str(e).replace(api_key, "***")
        raise HTTPException(status_code=502, detail=f"Failed to communicate with AI provider: {safe_error}")
