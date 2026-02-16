"""Tests for the intent parser (unit tests without API calls)."""

import pytest
from src.orchestrator.intent_parser import (
    ConversationHistory,
    ParsedIntent,
    VALID_ACTIONS,
)


def test_valid_actions_set():
    assert "code_change" in VALID_ACTIONS
    assert "operation" in VALID_ACTIONS
    assert "deploy" in VALID_ACTIONS
    assert "git" in VALID_ACTIONS
    assert "query" in VALID_ACTIONS
    assert "plan" in VALID_ACTIONS


def test_parsed_intent_defaults():
    intent = ParsedIntent(action="query")
    assert intent.action == "query"
    assert intent.project is None
    assert intent.prompt is None
    assert intent.confidence == 0.0
    assert intent.image_data is None


def test_parsed_intent_with_image():
    intent = ParsedIntent(action="code_change", project="test")
    intent.image_data = b"fake-image-data"
    assert intent.image_data == b"fake-image-data"


class TestConversationHistory:
    def test_add_and_get(self):
        h = ConversationHistory()
        h.add(1, "user", "hello")
        h.add(1, "assistant", "hi")
        assert len(h.get(1)) == 2
        assert h.get(1)[0]["role"] == "user"

    def test_empty_history(self):
        h = ConversationHistory()
        assert h.get(999) == []

    def test_max_messages(self):
        h = ConversationHistory(max_messages=3)
        for i in range(5):
            h.add(1, "user", f"msg {i}")
        assert len(h.get(1)) == 3
        assert h.get(1)[0]["content"] == "msg 2"

    def test_clear(self):
        h = ConversationHistory()
        h.add(1, "user", "hello")
        h.clear(1)
        assert h.get(1) == []

    def test_separate_users(self):
        h = ConversationHistory()
        h.add(1, "user", "user1")
        h.add(2, "user", "user2")
        assert len(h.get(1)) == 1
        assert len(h.get(2)) == 1
        assert h.get(1)[0]["content"] == "user1"
