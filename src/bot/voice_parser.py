"""Voice message parser – transcribes audio using Gemini's native multimodal.

Supports voice messages from both Telegram (.ogg/opus) and WhatsApp (.ogg/opus, .mp3).
Gemini handles transcription natively – no need for Whisper or separate ASR.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)

# MIME type mapping for common voice message formats
VOICE_MIME_TYPES = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mp3",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".amr": "audio/amr",     # WhatsApp older format
    ".3gp": "audio/3gpp",    # WhatsApp older format
}


class VoiceParser:
    """Transcribe voice messages using Gemini's native audio understanding."""

    def __init__(self, gemini: GeminiProvider, default_language: str = "es"):
        self._gemini = gemini
        self._default_language = default_language

    async def transcribe(
        self,
        audio_data: bytes,
        mime_type: str = "audio/ogg",
        language: str | None = None,
    ) -> str:
        """Transcribe audio data to text.

        Args:
            audio_data: Raw audio bytes
            mime_type: Audio MIME type
            language: Language hint (defaults to Spanish)

        Returns:
            Transcribed text, or empty string on failure.
        """
        lang = language or self._default_language

        logger.info(
            "Transcribing audio: %d bytes, mime=%s, lang=%s",
            len(audio_data), mime_type, lang,
        )

        text = await self._gemini.transcribe_audio(
            audio_data=audio_data,
            mime_type=mime_type,
            language_hint=lang,
        )

        if text:
            logger.info("Transcription result: %s", text[:100])
        else:
            logger.warning("Transcription returned empty result")

        return text.strip() if text else ""

    async def transcribe_and_parse(
        self,
        audio_data: bytes,
        mime_type: str = "audio/ogg",
        language: str | None = None,
    ) -> tuple[str, str]:
        """Transcribe audio and return both the raw transcription and a cleaned version.

        Returns:
            Tuple of (raw_transcription, cleaned_text)
        """
        raw = await self.transcribe(audio_data, mime_type, language)
        if not raw:
            return "", ""

        # Clean common transcription artifacts
        cleaned = raw.strip()
        # Remove common filler words at the start
        for filler in ["eh ", "ehm ", "um ", "uh ", "mm ", "mmm "]:
            if cleaned.lower().startswith(filler):
                cleaned = cleaned[len(filler):]

        return raw, cleaned.strip()

    @staticmethod
    def mime_type_from_extension(ext: str) -> str:
        """Get MIME type from file extension."""
        return VOICE_MIME_TYPES.get(ext.lower(), "audio/ogg")

    @staticmethod
    def mime_type_for_telegram() -> str:
        """Telegram sends voice messages as OGG/Opus."""
        return "audio/ogg"

    @staticmethod
    def mime_type_for_whatsapp() -> str:
        """WhatsApp sends voice messages as OGG/Opus."""
        return "audio/ogg"
