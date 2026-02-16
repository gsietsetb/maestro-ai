"""Tests for Telegram message formatters."""

import pytest
from src.bot.formatters import (
    escape_md,
    format_error,
    format_help,
    format_project_list,
    format_task_launched,
    format_task_result,
)


def test_escape_md_special_chars():
    assert escape_md("hello_world") == r"hello\_world"
    assert escape_md("test.py") == r"test\.py"
    assert escape_md("a+b=c") == r"a\+b\=c"


def test_escape_md_no_special():
    assert escape_md("hello") == "hello"


def test_format_task_launched():
    result = format_task_launched("plinng-web", "code_change", "abc-123")
    assert "plinng\\-web" in result
    assert "abc\\-123" in result


def test_format_task_result_success():
    result = format_task_result("project", "test", True, "All passed")
    assert "Completado" in result
    assert "All passed" in result


def test_format_task_result_failure():
    result = format_task_result("project", "test", False, "2 failed")
    assert "Error" in result


def test_format_task_result_truncates():
    long_output = "x" * 4000
    result = format_task_result("project", "test", True, long_output)
    assert "truncado" in result


def test_format_project_list():
    projects = {
        "test-project": {
            "stack": "nextjs",
            "aliases": ["test", "tp"],
        }
    }
    result = format_project_list(projects)
    assert "test\\-project" in result
    assert "nextjs" in result


def test_format_help():
    result = format_help()
    assert "Cursor Orchestrator" in result
    assert "/projects" in result


def test_format_error():
    result = format_error("something broke")
    assert "Error" in result
    assert "something broke" in result
