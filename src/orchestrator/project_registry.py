"""Project registry – loads projects.yaml and resolves projects by name or alias."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class ProjectRegistry:
    """In-memory registry of all known projects."""

    def __init__(self, yaml_path: str | Path = "projects.yaml"):
        self._path = Path(yaml_path)
        self._projects: dict[str, dict] = {}
        self._alias_map: dict[str, str] = {}  # alias -> canonical name
        self._load()

    def _load(self) -> None:
        """Load and index projects.yaml."""
        if not self._path.exists():
            logger.warning("Projects file not found: %s", self._path)
            return

        with open(self._path) as f:
            data = yaml.safe_load(f) or {}

        self._projects = data.get("projects", {})

        # Build alias map
        for name, info in self._projects.items():
            # Canonical name maps to itself
            self._alias_map[name.lower()] = name
            for alias in info.get("aliases", []):
                self._alias_map[alias.lower()] = name

        logger.info(
            "Loaded %d projects (%d aliases)", len(self._projects), len(self._alias_map)
        )

    def resolve(self, name_or_alias: str) -> Optional[dict]:
        """Resolve a project by name or alias. Returns project info dict or None."""
        canonical = self._alias_map.get(name_or_alias.lower())
        if canonical:
            return {**self._projects[canonical], "_name": canonical}
        return None

    def get(self, name: str) -> Optional[dict]:
        """Get project info by exact canonical name."""
        info = self._projects.get(name)
        if info:
            return {**info, "_name": name}
        return None

    def project_names(self) -> list[str]:
        """Return list of canonical project names."""
        return list(self._projects.keys())

    def all_projects(self) -> dict[str, dict]:
        """Return all projects as {name: info}."""
        return dict(self._projects)

    def all_aliases(self) -> dict[str, str]:
        """Return the full alias -> canonical name map."""
        return dict(self._alias_map)

    def reload(self) -> None:
        """Reload projects from disk."""
        self._projects.clear()
        self._alias_map.clear()
        self._load()
