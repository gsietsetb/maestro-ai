"""Tests for the project registry."""

import pytest
from src.orchestrator.project_registry import ProjectRegistry


@pytest.fixture
def registry():
    return ProjectRegistry("projects.yaml")


def test_loads_all_projects(registry):
    names = registry.project_names()
    assert len(names) == 10
    assert "plinng-web" in names
    assert "plinng-api-MARKETIQ" in names


def test_resolve_by_name(registry):
    info = registry.resolve("plinng-web")
    assert info is not None
    assert info["_name"] == "plinng-web"
    assert info["stack"] == "nextjs"


def test_resolve_by_alias(registry):
    info = registry.resolve("api")
    assert info is not None
    assert info["_name"] == "plinng-api-MARKETIQ"

    info = registry.resolve("expo")
    assert info is not None
    assert info["_name"] == "plinng-expo"

    info = registry.resolve("dive")
    assert info is not None
    assert info["_name"] == "divenamic"


def test_resolve_case_insensitive(registry):
    info = registry.resolve("API")
    assert info is not None
    assert info["_name"] == "plinng-api-MARKETIQ"


def test_resolve_unknown(registry):
    assert registry.resolve("nonexistent") is None


def test_all_projects(registry):
    projects = registry.all_projects()
    assert isinstance(projects, dict)
    assert "plinng-web" in projects
    assert "commands" in projects["plinng-web"]


def test_get_by_exact_name(registry):
    info = registry.get("plinng-web")
    assert info is not None
    assert info["_name"] == "plinng-web"

    assert registry.get("nonexistent") is None
