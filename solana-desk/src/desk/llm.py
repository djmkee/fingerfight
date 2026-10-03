"""Thin OpenAI-compatible chat client.

A role call is one system prompt plus one JSON message of stored tool results. There is no
function calling and no browsing, so the model can only answer about what it was handed.
"""

import httpx

from .settings import Settings


class LLMError(RuntimeError):
    """The LLM call failed. Callers fail closed."""


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = httpx.Client(
            timeout=timeout, transport=transport, headers={"Authorization": f"Bearer {api_key}"}
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMClient | None":
        if not settings.llm_configured:
            return None
        return cls(settings.llm_base_url or "", settings.llm_api_key or "", settings.llm_model or "")

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            response = self._client.post(self._url, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        if not isinstance(content, str):
            raise LLMError("reply has no text content")
        return content
