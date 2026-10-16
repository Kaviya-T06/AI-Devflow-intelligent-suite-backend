"""
Ollama Service — connects to a local or self-hosted Ollama instance.

Configuration via environment variables:
    OLLAMA_BASE_URL — base endpoint (default: http://localhost:11434)
    OLLAMA_MODEL    — model tag (default: llama3.2:1b)
    OLLAMA_TIMEOUT  — request timeout in seconds (default: 60.0)

Endpoints:
    POST /api/generate with { model, prompt, stream: false, format: "json" (optional) }
"""
import logging
from typing import Optional, Dict, Any

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


async def ask_ollama(
    prompt: str,
    json_response: bool = False,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> str:
    """
    Sends a prompt to the configured local Ollama instance and returns the generated text.

    Args:
        prompt: The full prompt string to send to the model.
        json_response: If True, sets format="json" in the request payload.
        model: Optional override for the model tag (defaults to settings.OLLAMA_MODEL).
        timeout: Optional override for request timeout (defaults to settings.OLLAMA_TIMEOUT).

    Returns:
        The generated text string from the Ollama model.

    Raises:
        HTTPException(503): When Ollama daemon cannot be reached (connection error).
        HTTPException(504): When Ollama request times out.
        HTTPException(502): On non-success HTTP status, invalid JSON, or empty model output.
    """
    base_url = (settings.OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")
    active_model = (model or settings.OLLAMA_MODEL or "llama3.2:1b").strip()
    timeout_sec = float(timeout or settings.OLLAMA_TIMEOUT or 60.0)

    logger.info(
        "[Ollama Service] Request starting | BaseUrl: %s | Model: %s | Json: %s | Timeout: %.1fs",
        base_url,
        active_model,
        json_response,
        timeout_sec,
    )

    url = f"{base_url}/api/generate"
    payload: Dict[str, Any] = {
        "model": active_model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "15m",
        "options": {
            "temperature": 0.2,
            "num_predict": 400 if json_response else 300,
            "num_ctx": 2048,
        },
    }
    if json_response:
        payload["format"] = "json"

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.post(url, json=payload)

    except (httpx.ConnectError, httpx.ConnectTimeout) as conn_err:
        logger.error(
            "[Ollama Service] Connection failed | BaseUrl: %s | Error: %s",
            base_url,
            str(conn_err),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Ollama service unavailable at {base_url}. "
                f"Ensure Ollama is installed and running locally."
            ),
        )

    except httpx.TimeoutException:
        logger.error(
            "[Ollama Service] Request timed out | BaseUrl: %s | Model: %s | Timeout: %.1fs",
            base_url,
            active_model,
            timeout_sec,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"Ollama request timed out after {timeout_sec:.0f}s "
                f"(Model: {active_model})."
            ),
        )

    except httpx.RequestError as req_err:
        logger.error(
            "[Ollama Service] Network error | BaseUrl: %s | Error: %s",
            base_url,
            str(req_err),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with Ollama service: {str(req_err)}",
        )

    except Exception as exc:
        logger.error(
            "[Ollama Service] Unexpected client error: %s",
            str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unexpected error communicating with Ollama: {str(exc)}",
        )

    # ── Check HTTP status ────────────────────────────────────────────────────
    if not response.is_success:
        err_body = response.text[:300]
        logger.error(
            "[Ollama Service] HTTP %d | BaseUrl: %s | Model: %s | Body: %s",
            response.status_code,
            base_url,
            active_model,
            err_body,
        )
        if response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"Ollama model '{active_model}' was not found. "
                    f"Run `ollama pull {active_model}` or update OLLAMA_MODEL in .env."
                ),
            )
        out_status = status.HTTP_429_TOO_MANY_REQUESTS if response.status_code == 429 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(
            status_code=out_status,
            detail=f"Ollama returned HTTP {response.status_code}: {err_body}",
        )

    # ── Parse response JSON ──────────────────────────────────────────────────
    try:
        data = response.json()
    except Exception as parse_err:
        logger.error(
            "[Ollama Service] Failed to parse JSON response: %s | RawText: %s",
            str(parse_err),
            response.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ollama returned invalid JSON response.",
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ollama returned an unexpected response structure.",
        )

    if "error" in data:
        err_msg = str(data["error"])
        logger.error(
            "[Ollama Service] Ollama reported error in payload: %s",
            err_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama returned error: {err_msg}",
        )

    generated_text = data.get("response")
    if generated_text is None or not str(generated_text).strip():
        logger.warning(
            "[Ollama Service] Empty response field in payload | Keys: %s",
            list(data.keys()),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ollama returned an empty response.",
        )

    result_str = str(generated_text).strip()
    logger.info(
        "[Ollama Service] Success | Model: %s | Length: %d chars",
        active_model,
        len(result_str),
    )
    return result_str


async def check_ollama_health() -> Dict[str, Any]:
    """
    Lightweight health check against the Ollama /api/tags endpoint.
    Returns status dict indicating reachability and available models.
    """
    base_url = (settings.OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")
    active_model = (settings.OLLAMA_MODEL or "llama3.2:1b").strip()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            if resp.is_success:
                data = resp.json()
                models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
                return {
                    "available": True,
                    "base_url": base_url,
                    "target_model": active_model,
                    "model_installed": any(active_model in m for m in models),
                    "installed_models": models,
                }
    except Exception as exc:
        return {
            "available": False,
            "base_url": base_url,
            "target_model": active_model,
            "error": str(exc),
        }

    return {
        "available": False,
        "base_url": base_url,
        "target_model": active_model,
    }
