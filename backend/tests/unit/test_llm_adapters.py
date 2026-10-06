"""LLM provider adapters against mocked HTTP (no real provider is contacted)."""

import json

import httpx
import pytest

from app.config import Settings
from app.services.rag import generation
from app.services.rag.llm import AnthropicClient, ChatMessage, LLMError, OpenAICompatibleClient
from app.services.rag.retrieval import Candidate

KEY = "sk-unit-test-key-0123456789"


def _recorder(response: httpx.Response):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return seen, httpx.MockTransport(handler)


def test_anthropic_request_format_and_parsing():
    seen, transport = _recorder(httpx.Response(200, json={
        "content": [{"type": "text", "text": "Answer [S1]"}, {"type": "tool_use", "id": "x"}]}))
    client = AnthropicClient(Settings(llm_provider="anthropic", llm_model="claude-test", llm_api_key=KEY),
                             transport=transport)
    text = client.complete("system rules", [ChatMessage("user", "question")], max_tokens=50)
    assert text == "Answer [S1]"
    request = seen[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == KEY and request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["system"] == "system rules" and body["messages"] == [{"role": "user", "content": "question"}]
    assert body["model"] == "claude-test" and body["max_tokens"] == 50 and body["temperature"] == 0.0


def test_openai_compatible_request_format_and_parsing():
    seen, transport = _recorder(httpx.Response(200, json={"choices": [{"message": {"content": " Hi [S2] "}}]}))
    settings = Settings(llm_provider="openai_compatible", llm_model="llama3.1", llm_api_key=None,
                        llm_base_url="http://localhost:11434/v1/")
    text = OpenAICompatibleClient(settings, transport=transport).complete(
        "sys", [ChatMessage("user", "q")], max_tokens=10)
    assert text == "Hi [S2]"
    assert str(seen[0].url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in seen[0].headers  # local servers need no key
    assert json.loads(seen[0].content)["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize("response,fragment", [
    (httpx.Response(401, json={"error": "bad key"}), "rejected the configured API key"),
    (httpx.Response(429), "rate limiting"),
    (httpx.Response(500, text=f"echo {KEY}"), "HTTP 500"),
    (httpx.Response(200, text="not json"), "invalid JSON"),
    (httpx.Response(200, json={"unexpected": True}), "unexpected response format"),
])
def test_provider_errors_are_mapped_without_leaking_the_key(response, fragment):
    _, transport = _recorder(response)
    client = AnthropicClient(Settings(llm_provider="anthropic", llm_model="m", llm_api_key=KEY), transport=transport)
    with pytest.raises(LLMError) as excinfo:
        client.complete("s", [ChatMessage("user", "q")], max_tokens=5)
    assert fragment in excinfo.value.message and KEY not in excinfo.value.message


def test_timeouts_are_reported():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = AnthropicClient(Settings(llm_provider="anthropic", llm_model="m", llm_api_key=KEY),
                             transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="did not respond in time"):
        client.complete("s", [ChatMessage("user", "q")], max_tokens=5)


def test_failed_provider_falls_back_to_extractive_answer():
    class Down:
        name = "down"

        def complete(self, *args, **kwargs):
            raise LLMError("The language model service could not be reached.")

    evidence = [Candidate(chunk_id="c1", document_id="d1", document_title="SID", filename="sid.pdf", doc_type="sid",
                          as_of_date=None, fund_id=None, is_synthetic=True, page_start=2, page_end=2,
                          section_heading="Load structure", content="Exit load: 0.25% if redeemed within 30 days.",
                          is_table=False, flags=[], supported=True)]
    answer = generation.safe_generate(Down(), Settings(), "What is the exit load?", evidence, [], [])
    assert answer.mode == "extractive" and "0.25%" in answer.text
    assert any("Language model unavailable" in w for w in answer.warnings)
