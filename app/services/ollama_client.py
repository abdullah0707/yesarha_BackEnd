import time
import requests
from typing import Optional

from app.core.config import settings
from app.core.responses import AppError, ErrorCodes


class OllamaClient:
    """
    Thin client around the Ollama HTTP API.
    base_url can be overridden per-model (Model Registry endpoint_url),
    falling back to settings.OLLAMA_BASE_URL (local during development,
    cloud URL after deployment).
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")

    def chat(self, model: str, messages: list[dict], stream: bool = False, options: Optional[dict] = None) -> dict:
        """
        Calls POST /api/chat and returns a normalized dict:
        {
            "content": str,
            "tokens_input": int,
            "tokens_output": int,
            "latency_ms": int,
            "raw": <full ollama response>
        }
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if options:
            payload["options"] = options

        start = time.perf_counter()

        try:
            resp = requests.post(url, json=payload, timeout=120)
        except requests.exceptions.ConnectionError:
            raise AppError(ErrorCodes.OLLAMA_UNREACHABLE, f"Cannot reach Ollama at {self.base_url}", 503)
        except requests.exceptions.Timeout:
            raise AppError(ErrorCodes.OLLAMA_UNREACHABLE, "Ollama request timed out", 504)

        latency_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code != 200:
            raise AppError(ErrorCodes.MODEL_UNAVAILABLE, f"Ollama error: {resp.status_code} {resp.text[:300]}", 502)

        data = resp.json()

        content = ""
        if isinstance(data, dict):
            message = data.get("message") or {}
            content = message.get("content", "")

        tokens_input = data.get("prompt_eval_count", 0) or 0
        tokens_output = data.get("eval_count", 0) or 0

        return {
            "content": content,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "latency_ms": latency_ms,
            "raw": data
        }

    def list_models(self) -> list[str]:
        url = f"{self.base_url}/api/tags"
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            return [m.get("name") for m in data.get("models", [])]
        except Exception:
            return []
