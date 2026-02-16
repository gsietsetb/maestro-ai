"""Tests for the voice parser."""

import pytest
from src.bot.voice_parser import VoiceParser, VOICE_MIME_TYPES


def test_mime_type_from_extension():
    assert VoiceParser.mime_type_from_extension(".ogg") == "audio/ogg"
    assert VoiceParser.mime_type_from_extension(".mp3") == "audio/mp3"
    assert VoiceParser.mime_type_from_extension(".wav") == "audio/wav"
    assert VoiceParser.mime_type_from_extension(".m4a") == "audio/mp4"
    assert VoiceParser.mime_type_from_extension(".amr") == "audio/amr"


def test_mime_type_for_telegram():
    assert VoiceParser.mime_type_for_telegram() == "audio/ogg"


def test_mime_type_for_whatsapp():
    assert VoiceParser.mime_type_for_whatsapp() == "audio/ogg"


def test_unknown_extension_defaults_to_ogg():
    assert VoiceParser.mime_type_from_extension(".xyz") == "audio/ogg"


def test_voice_mime_types_mapping():
    assert ".ogg" in VOICE_MIME_TYPES
    assert ".opus" in VOICE_MIME_TYPES
    assert ".mp3" in VOICE_MIME_TYPES
    assert ".flac" in VOICE_MIME_TYPES
    assert ".3gp" in VOICE_MIME_TYPES
