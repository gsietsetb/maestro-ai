"""Gemini AI provider – REST-based, same pattern as plinng-api-MARKETIQ.

Supports:
- Text generation (generate)
- Structured JSON output with responseSchema (generate_json)
- Multimodal: inline images and audio (generate_multimodal)
- Audio transcription (transcribe_audio)

Uses direct REST calls to generativelanguage.googleapis.com (no SDK).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _repair_json(text: str) -> dict | list | None:
    """Try to repair truncated or malformed JSON from Gemini.

    Handles common issues:
    - Trailing commas: {"action": "git",}  or  {"a": 1,
    - Unclosed strings/objects: {"action": "gi
    - Markdown wrappers: ```json { ... } ```
    """
    if not text:
        return None

    # Strip markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Remove trailing commas before } or ]
    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Try to close unclosed objects/arrays
    open_braces = fixed.count("{") - fixed.count("}")
    open_brackets = fixed.count("[") - fixed.count("]")
    if open_braces > 0 or open_brackets > 0:
        # Remove trailing partial key-value
        # e.g. {"action": "git", "proj  →  {"action": "git"
        truncated = re.sub(r',\s*"[^"]*$', "", fixed)
        truncated = re.sub(r',\s*$', "", truncated)
        truncated += "}" * open_braces + "]" * open_brackets
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    # Last resort: extract the first complete JSON object
    match = re.search(r"\{[^{}]*\}", fixed)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


class GeminiProvider:
    """Gemini AI provider via direct REST API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        base_url: str = GEMINI_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    # ── Text generation ───────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        disable_thinking: bool = False,
    ) -> str:
        """Generate text. Returns empty string on failure (graceful degradation).

        Args:
            disable_thinking: Set True for simple tasks (summaries, short answers)
                to avoid wasting tokens on internal thinking.
        """
        if not self.configured:
            return ""

        body = self._build_body(prompt, system_prompt, temperature, max_tokens)
        if disable_thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        response = await self._request(body)
        return self._extract_text(response) if response else ""

    # ── Structured JSON output ────────────────────────────────────────────

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Generate structured JSON with optional responseSchema.

        Uses responseMimeType: application/json + retry with JSON repair.
        Returns None on failure after all retries.
        """
        if not self.configured:
            return None

        # Try up to 2 times: first with schema, then without (fallback)
        for attempt in range(2):
            body = self._build_body(prompt, system_prompt, temperature, max_tokens)

            # Enable structured JSON output
            body["generationConfig"]["responseMimeType"] = "application/json"
            if response_schema and attempt == 0:
                body["generationConfig"]["responseSchema"] = response_schema

            response = await self._request(body)
            if not response:
                continue

            text = self._extract_text(response)
            if not text:
                continue

            # Try direct parse
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Gemini JSON parse error (attempt %d): %s (text: %s)",
                    attempt + 1, e, text[:200],
                )

            # Try repair
            repaired = _repair_json(text)
            if repaired is not None:
                logger.info("Gemini JSON repaired successfully")
                return repaired

            logger.warning("JSON repair failed, retrying without schema...")

        return None

    # ── Multimodal (text + image/audio) ───────────────────────────────────

    async def generate_multimodal(
        self,
        prompt: str,
        media_data: bytes,
        mime_type: str,
        system_prompt: str | None = None,
        temperature: float = 0.5,
        max_tokens: int = 4096,
    ) -> str:
        """Generate with inline media (image or audio).

        Args:
            prompt: Text instruction
            media_data: Raw bytes of the media file
            mime_type: MIME type (e.g. "image/jpeg", "audio/ogg", "audio/mp3")
            system_prompt: Optional system instruction
            temperature: Sampling temperature
            max_tokens: Max output tokens
        """
        if not self.configured:
            return ""

        contents = []

        # System prompt as conversation preamble
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Entendido."}]})

        # User message with text + media
        b64_data = base64.b64encode(media_data).decode("utf-8")
        contents.append({
            "role": "user",
            "parts": [
                {"text": prompt},
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": b64_data,
                    }
                },
            ],
        })

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "topP": 0.95,
            },
        }

        response = await self._request(body)
        return self._extract_text(response) if response else ""

    # ── Audio transcription ───────────────────────────────────────────────

    async def transcribe_audio(
        self,
        audio_data: bytes,
        mime_type: str = "audio/ogg",
        language_hint: str = "es",
    ) -> str:
        """Transcribe audio using Gemini's native multimodal capabilities.

        Args:
            audio_data: Raw audio bytes
            mime_type: Audio MIME type (audio/ogg, audio/mp3, audio/wav, etc.)
            language_hint: Expected language for better transcription

        Returns:
            Transcribed text, or empty string on failure.
        """
        prompt = (
            f"Transcribe the following audio message exactly as spoken. "
            f"The audio is likely in {language_hint}. "
            f"Return ONLY the transcribed text, nothing else. "
            f"Do not add quotes, labels, or explanations."
        )

        return await self.generate_multimodal(
            prompt=prompt,
            media_data=audio_data,
            mime_type=mime_type,
            temperature=0.1,
            max_tokens=2048,
        )

    # ── JSON multimodal (image + structured output) ───────────────────────

    async def generate_json_multimodal(
        self,
        prompt: str,
        media_data: bytes,
        mime_type: str,
        system_prompt: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Generate structured JSON from media input (e.g. screenshot analysis)."""
        if not self.configured:
            return None

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Entendido."}]})

        b64_data = base64.b64encode(media_data).decode("utf-8")
        contents.append({
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inlineData": {"mimeType": mime_type, "data": b64_data}},
            ],
        })

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        }

        if response_schema:
            body["generationConfig"]["responseSchema"] = response_schema

        response = await self._request(body)
        if not response:
            return None

        text = self._extract_text(response)
        if not text:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = _repair_json(text)
            if repaired is not None:
                return repaired
            return None

    # ── Internals ─────────────────────────────────────────────────────────

    def _build_body(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Build standard generateContent request body."""
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Entendido."}]})

        contents.append({"role": "user", "parts": [{"text": prompt}]})

        return {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "topP": 0.95,
            },
        }

    async def _request(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """Make request to Gemini API with retries."""
        url = f"{self._base_url}/models/{self._model}:generateContent"

        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(
                    url,
                    json=body,
                    params={"key": self._api_key},
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Gemini HTTP error (attempt %d/%d): %s %s",
                    attempt + 1, self._max_retries,
                    e.response.status_code, e.response.text[:300],
                )
                if e.response.status_code in (429, 500, 503) and attempt < self._max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None

            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                logger.warning("Gemini timeout (attempt %d/%d): %s", attempt + 1, self._max_retries, e)
                if attempt < self._max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None

            except Exception as e:
                logger.error("Gemini unexpected error: %s", e)
                return None

        return None

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        """Extract text from Gemini generateContent response."""
        try:
            candidates = response.get("candidates", [])
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return ""
            return parts[0].get("text", "")
        except (IndexError, KeyError, TypeError):
            return ""

    # ── Health check ──────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check if Gemini API is accessible."""
        if not self.configured:
            return False
        result = await self.generate("Say OK", temperature=0.0, max_tokens=10)
        return bool(result)
