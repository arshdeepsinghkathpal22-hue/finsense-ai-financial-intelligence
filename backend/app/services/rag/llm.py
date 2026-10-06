"""Thin LLM provider adapters.

Two adapters cover most setups:

* ``anthropic`` - the Anthropic Messages API (``POST /v1/messages``).
* ``openai_compatible`` - any server exposing ``POST {base}/chat/completions``
  (OpenAI, Groq, Together, or a *local* model through Ollama / LM Studio,
  e.g. ``LLM_BASE_URL=http://localhost:11434/v1``).

The rest of the application depends only on :class:`LLMClient`, so tests
substitute a deterministic fake without network access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.core.errors import ServiceUnavailableError

logger = logging.getLogger("finsense.llm")


class LLMError(ServiceUnavailableError):
    pass


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int,
                 temperature: float = 0.0) -> str: ...


class AnthropicClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        if settings.llm_api_key is None:
            raise LLMError("LLM_API_KEY is required for the Anthropic provider.")
        self.name = f"anthropic/{settings.llm_model}"
        self._model = settings.llm_model
        self._base = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/")
        self._client = httpx.Client(
            timeout=settings.llm_timeout_s,
            transport=transport,
            headers={
                "x-api-key": settings.llm_api_key.get_secret_value(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int,
                 temperature: float = 0.0) -> str:
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        data = _post(self._client, f"{self._base}/v1/messages", payload)
        try:
            return "".join(block["text"] for block in data["content"] if block.get("type") == "text").strip()
        except (KeyError, TypeError) as exc:
            raise LLMError("The language model returned an unexpected response format.") from exc


class OpenAICompatibleClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"content-type": "application/json"}
        if settings.llm_api_key is not None and settings.llm_api_key.get_secret_value():
            headers["authorization"] = f"Bearer {settings.llm_api_key.get_secret_value()}"
        self.name = f"openai-compatible/{settings.llm_model}"
        self._model = settings.llm_model
        self._base = settings.llm_base_url.rstrip("/")
        self._client = httpx.Client(timeout=settings.llm_timeout_s, transport=transport, headers=headers)

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int,
                 temperature: float = 0.0) -> str:
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
        }
        data = _post(self._client, f"{self._base}/chat/completions", payload)
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("The language model returned an unexpected response format.") from exc


def _post(client: httpx.Client, url: str, payload: dict) -> dict:
    try:
        response = client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMError("The language model did not respond in time.") from exc
    except httpx.HTTPError as exc:
        raise LLMError("The language model service could not be reached.") from exc
    if response.status_code in (401, 403):
        raise LLMError("The language model rejected the configured API key.")
    if response.status_code == 429:
        raise LLMError("The language model provider is rate limiting requests; try again shortly.")
    if response.status_code >= 400:
        # Provider error bodies can echo request content; log only the status.
        logger.warning("LLM provider returned HTTP %s", response.status_code)
        raise LLMError(f"The language model returned an error (HTTP {response.status_code}).")
    try:
        return response.json()
    except ValueError as exc:
        raise LLMError("The language model returned invalid JSON.") from exc


_client_cache: dict[str, LLMClient] = {}


def get_llm_client(settings: Settings) -> LLMClient | None:
    """Returns the configured client, or None when no LLM is configured."""
    if not settings.llm_configured:
        return None
    key = f"{settings.llm_provider}:{settings.llm_model}:{settings.llm_base_url}"
    if key not in _client_cache:
        if settings.llm_provider == "anthropic":
            _client_cache[key] = AnthropicClient(settings)
        else:
            _client_cache[key] = OpenAICompatibleClient(settings)
    return _client_cache[key]
