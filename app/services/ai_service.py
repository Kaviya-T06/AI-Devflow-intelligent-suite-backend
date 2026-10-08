"""
AI Service — sends prompts to the configured AI provider (Google Gemini API).

Configuration is fully driven by environment variables:
    AI_API_KEY    — Google AI Studio API key (required)
    AI_MODEL      — Gemini model name (default: gemini-2.0-flash)
    AI_PROVIDER   — provider identifier for logging (default: google-gemini)

NEVER hardcode API keys or model names here.
"""
import asyncio
import json
import logging

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_key(api_key: str) -> str:
    """Returns a redacted version of the API key safe for logging."""
    if not api_key or len(api_key) < 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _classify_http_error(status_code: int) -> str:
    """Returns a human-readable label for common HTTP error codes."""
    return {
        400: "BAD_REQUEST — malformed request body",
        401: "UNAUTHORIZED — invalid or missing API key",
        403: "FORBIDDEN — API key does not have access to this model",
        404: "NOT_FOUND — model name not found or incorrect (check AI_MODEL in .env)",
        429: "RATE_LIMITED — too many requests / quota exceeded",
        500: "PROVIDER_INTERNAL_ERROR — upstream server error",
        503: "PROVIDER_OVERLOADED — model currently overloaded, will retry",
    }.get(status_code, f"HTTP_{status_code}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ask_llm(prompt: str, json_response: bool = False) -> str:
    """
    Sends a prompt to the configured AI provider (Google Gemini API) and returns
    the generated text.

    Args:
        prompt: The full prompt string to send to the model.
        json_response: If True, requests JSON-formatted output from the model.

    Returns:
        The generated text string from the AI model.

    Raises:
        HTTPException(500): AI_API_KEY is not configured.
        HTTPException(502): Provider returned an error or unexpected response.
    """
    api_key = settings.AI_API_KEY.strip()
    model = settings.AI_MODEL.strip() or "gemini-3.5-flash"
    provider = settings.AI_PROVIDER.strip() or "google-gemini"

    # ── Validate configuration ──────────────────────────────────────────────
    if not api_key:
        logger.error(
            "[AI Service] MISSING API KEY — AI_API_KEY is not set in .env. "
            "Provider: %s | Model: %s",
            provider, model
        )
        raise HTTPException(
            status_code=500,
            detail="AI_API_KEY is not configured. Set it in the backend .env file."
        )

    logger.info(
        "[AI Service] Request starting | Provider: %s | Model: %s | Key: %s",
        provider, model, _safe_key(api_key)
    )

    # ── Build request ────────────────────────────────────────────────────────
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models"
        f"/{model}:generateContent"
    )
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    # if json_response:
    #     payload["generationConfig"] = {"responseMimeType": "application/json"}

    # ── Retry loop (up to 3 attempts on 503 overload) ───────────────────────
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json=payload, timeout=45.0
                )

            # ── Handle HTTP errors ───────────────────────────────────────────
            if not response.is_success:
                status = response.status_code
                error_label = _classify_http_error(status)
                try:
                    body = response.json()
                    provider_msg = (
                        body.get("error", {}).get("message", response.text[:300])
                    )
                except Exception:
                    provider_msg = response.text[:300]

                logger.error(
                    "[AI Service] Provider error | Provider: %s | Model: %s | "
                    "Status: %s (%s) | Attempt: %d/%d | Message: %s",
                    provider, model, status, error_label,
                    attempt, max_retries, provider_msg
                )

                # Retry on 503 (overloaded)
                if status == 503 and attempt < max_retries:
                    wait_secs = attempt * 2
                    logger.warning(
                        "[AI Service] Model overloaded (503). Retrying in %ds "
                        "(attempt %d/%d)...",
                        wait_secs, attempt, max_retries
                    )
                    await asyncio.sleep(wait_secs)
                    continue

                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"AI provider error [{error_label}]: {provider_msg}"
                    )
                )

            # ── Parse successful response ────────────────────────────────────
            data = response.json()

            # Check for provider-level error embedded in 200 response
            if "error" in data:
                err_msg = data["error"].get("message", str(data["error"]))
                logger.error(
                    "[AI Service] Provider returned error in 200 body | "
                    "Provider: %s | Model: %s | Error: %s",
                    provider, model, err_msg
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"AI provider returned an error: {err_msg}"
                )

            candidates = data.get("candidates")
            if not candidates or len(candidates) == 0:
                # Could be a safety block
                block_reason = data.get("promptFeedback", {}).get(
                    "blockReason", "unknown"
                )
                logger.error(
                    "[AI Service] Empty candidates in response | "
                    "Provider: %s | Model: %s | BlockReason: %s",
                    provider, model, block_reason
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"AI provider returned no candidates. "
                        f"Block reason: {block_reason}"
                    )
                )

            try:
                text = candidates[0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError, TypeError) as parse_err:
                logger.error(
                    "[AI Service] Unexpected response structure | "
                    "Provider: %s | Model: %s | ParseError: %s | "
                    "ResponseKeys: %s",
                    provider, model, parse_err, list(data.keys())
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "AI provider returned an unexpected response structure. "
                        "Check backend logs for details."
                    )
                )

            if not text or not text.strip():
                logger.warning(
                    "[AI Service] Provider returned empty text | "
                    "Provider: %s | Model: %s",
                    provider, model
                )
                raise HTTPException(
                    status_code=502,
                    detail="AI provider returned an empty response."
                )

            logger.info(
                "[AI Service] Success | Provider: %s | Model: %s | "
                "ResponseLength: %d chars",
                provider, model, len(text)
            )
            return text

        except HTTPException:
            raise  # Re-raise our own controlled errors

        except httpx.TimeoutException:
            logger.error(
                "[AI Service] Request timed out | Provider: %s | Model: %s | "
                "Attempt: %d/%d",
                provider, model, attempt, max_retries
            )
            if attempt < max_retries:
                await asyncio.sleep(attempt * 2)
                continue
            raise HTTPException(
                status_code=502,
                detail=(
                    f"AI provider request timed out after 45s "
                    f"(Provider: {provider}, Model: {model})"
                )
            )

        except httpx.RequestError as req_err:
            safe_err = str(req_err).replace(api_key, "***")
            logger.error(
                "[AI Service] Network/connection error | Provider: %s | "
                "Model: %s | Error: %s",
                provider, model, safe_err
            )
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to AI provider: {safe_err}"
            )

        except Exception as exc:
            safe_err = str(exc).replace(api_key, "***")
            logger.error(
                "[AI Service] Unexpected error | Provider: %s | Model: %s | "
                "Error: %s",
                provider, model, safe_err, exc_info=True
            )
            raise HTTPException(
                status_code=502,
                detail=f"Unexpected error communicating with AI provider: {safe_err}"
            )

    # Should never reach here, but guard anyway
    raise HTTPException(
        status_code=502,
        detail="AI provider failed after all retries."
    )
