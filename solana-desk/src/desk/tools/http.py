"""JSON over HTTP: timeouts, a short retry on 429/5xx, and every failure as a ToolError."""

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import ToolError

USER_AGENT = "solana-paper-desk/0.1"


class JsonHttp:
    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None, timeout: float = 10.0,
                 retries: int = 2, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.base_url = base_url.rstrip("/")
        parts = urlsplit(base_url)
        # Errors name the host only: RPC URLs such as Helius carry an API key in the query string.
        self.label = parts.netloc or base_url
        self._query = parts.query
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})},
            timeout=timeout,
            transport=transport,
        )
        self._retries = retries
        self._sleep = sleep

    def get(self, path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: Any) -> tuple[int, Any]:
        return self._request("POST", path, json=payload)

    def _request(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        url = f"{self.base_url}{path}" if path else self.base_url
        problem = "no response"
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleep(0.5 * 2 ** (attempt - 1))
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                problem = f"{type(exc).__name__}: {self._redact(str(exc))}"
                continue
            if response.status_code == 429 or response.status_code >= 500:
                problem = f"HTTP {response.status_code}"
                continue
            try:
                return response.status_code, response.json()
            except ValueError:
                raise ToolError(f"{self.label}: non-JSON response (HTTP {response.status_code})") from None
        raise ToolError(f"{self.label}: {problem}")

    def _redact(self, text: str) -> str:
        return text.replace(self._query, "<redacted>") if self._query else text
