"""
Ollama Client مع دعم Streaming كامل
"""
import json
import time
from typing import Generator, Optional, AsyncGenerator
import requests

from app.core.config import settings
from app.core.responses import AppError, ErrorCodes


def _resolve_ollama_url(base_url: Optional[str]) -> str:
    if base_url:
        return base_url.rstrip("/")
    try:
        from app.services.runtime_config import runtime_cfg
        return runtime_cfg.get_ollama_url().rstrip("/")
    except Exception:
        return settings.OLLAMA_BASE_URL.rstrip("/")


class OllamaClient:

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = _resolve_ollama_url(base_url)

    # ── Non-streaming ─────────────────────────────────────────────

    def chat(self, model: str, messages: list[dict],
             stream: bool = False, options: Optional[dict] = None,
             tools: Optional[list[dict]] = None,
             timeout: int = None) -> dict:
        url = f"{self.base_url}/api/chat"
        payload = {"model": model, "messages": messages, "stream": False}
        if options:
            payload["options"] = options
        if tools:
            payload["tools"] = tools

        start = time.perf_counter()
        try:
            resp = requests.post(url, json=payload,
                                 timeout=timeout or settings.CORE_MODEL_TIMEOUT)
        except requests.exceptions.ConnectionError:
            raise AppError(ErrorCodes.OLLAMA_UNREACHABLE,
                           f"Cannot reach Ollama at {self.base_url}", 503)
        except requests.exceptions.Timeout:
            raise AppError(ErrorCodes.OLLAMA_UNREACHABLE, "Ollama request timed out", 504)

        latency_ms = int((time.perf_counter() - start) * 1000)

        if resp.status_code != 200:
            raise AppError(ErrorCodes.MODEL_UNAVAILABLE,
                           f"Ollama error {resp.status_code}: {resp.text[:300]}", 502)

        data = resp.json()
        message = data.get("message") or {}
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])  # صيغة Ollama البنيوية الحقيقية

        return {
            "content": content,
            "tool_calls": tool_calls,
            "tokens_input": data.get("prompt_eval_count", 0) or 0,
            "tokens_output": data.get("eval_count", 0) or 0,
            "latency_ms": latency_ms,
            "raw": data,
        }

    # ── Streaming ─────────────────────────────────────────────────

    def chat_stream(self, model: str, messages: list[dict],
                    options: Optional[dict] = None,
                    tools: Optional[list[dict]] = None,
                    timeout: int = None) -> Generator[dict, None, None]:
        """
        يُرجع generator يُنتج chunks:
        {"type": "token", "content": "..."}
        {"type": "done", "tokens_input": N, "tokens_output": N, "latency_ms": N}
        {"type": "error", "code": "...", "message": "..."}
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if options:
            payload["options"] = options
        if tools:
            payload["tools"] = tools

        start = time.perf_counter()

        try:
            with requests.post(
                url, json=payload,
                timeout=timeout or settings.CORE_MODEL_TIMEOUT,
                stream=True
            ) as resp:

                if resp.status_code != 200:
                    yield {
                        "type": "error",
                        "code": ErrorCodes.MODEL_UNAVAILABLE,
                        "message": f"Ollama error {resp.status_code}"
                    }
                    return

                tokens_input = 0
                tokens_output = 0

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if data.get("done"):
                        tokens_input = data.get("prompt_eval_count", 0) or 0
                        tokens_output = data.get("eval_count", 0) or 0
                        latency_ms = int((time.perf_counter() - start) * 1000)
                        yield {
                            "type": "done",
                            "tokens_input": tokens_input,
                            "tokens_output": tokens_output,
                            "latency_ms": latency_ms,
                        }
                        return

                    content = (data.get("message") or {}).get("content", "")
                    if content:
                        yield {"type": "token", "content": content}

        except requests.exceptions.ConnectionError:
            yield {"type": "error", "code": ErrorCodes.OLLAMA_UNREACHABLE,
                   "message": f"Cannot reach Ollama at {self.base_url}"}
        except requests.exceptions.Timeout:
            yield {"type": "error", "code": ErrorCodes.OLLAMA_UNREACHABLE,
                   "message": "Ollama request timed out"}

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            return [m.get("name") for m in resp.json().get("models", [])]
        except Exception:
            return []

    def is_online(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.ok
        except Exception:
            return False
