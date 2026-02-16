"""Tests for the Gemini provider (unit tests without API calls)."""

import pytest
from src.providers.gemini import GeminiProvider


def test_not_configured_without_key():
    provider = GeminiProvider(api_key="")
    assert not provider.configured


def test_configured_with_key():
    provider = GeminiProvider(api_key="test-key")
    assert provider.configured


def test_build_body_simple():
    provider = GeminiProvider(api_key="test")
    body = provider._build_body("hello", None, 0.5, 100)
    assert "contents" in body
    assert body["contents"][-1]["parts"][0]["text"] == "hello"
    assert body["generationConfig"]["temperature"] == 0.5


def test_build_body_with_system_prompt():
    provider = GeminiProvider(api_key="test")
    body = provider._build_body("hello", "system instruction", 0.5, 100)
    # system prompt creates 2 messages before the user message
    assert len(body["contents"]) == 3
    assert body["contents"][0]["parts"][0]["text"] == "system instruction"
    assert body["contents"][1]["role"] == "model"
    assert body["contents"][2]["parts"][0]["text"] == "hello"


def test_extract_text_valid():
    response = {
        "candidates": [{
            "content": {
                "parts": [{"text": "Hello world"}]
            }
        }]
    }
    assert GeminiProvider._extract_text(response) == "Hello world"


def test_extract_text_empty_candidates():
    assert GeminiProvider._extract_text({"candidates": []}) == ""


def test_extract_text_no_candidates():
    assert GeminiProvider._extract_text({}) == ""


def test_extract_text_malformed():
    assert GeminiProvider._extract_text({"candidates": [{"content": {}}]}) == ""


@pytest.mark.asyncio
async def test_generate_returns_empty_when_not_configured():
    provider = GeminiProvider(api_key="")
    result = await provider.generate("test")
    assert result == ""


@pytest.mark.asyncio
async def test_generate_json_returns_none_when_not_configured():
    provider = GeminiProvider(api_key="")
    result = await provider.generate_json("test")
    assert result is None


@pytest.mark.asyncio
async def test_transcribe_returns_empty_when_not_configured():
    provider = GeminiProvider(api_key="")
    result = await provider.transcribe_audio(b"audio-data")
    assert result == ""
