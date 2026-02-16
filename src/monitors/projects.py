"""Local project monitor – scans git repos for changes and state.

Runs as a background task, periodically checking all registered projects
for git status, branch info, uncommitted changes, and recent commits.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.events import EventBus, EventType

logger = logging.getLogger(__name__)


@dataclass
class ProjectState:
    """Current state of a local project."""

    name: str
    path: str
    branch: str = ""
    has_uncommitted: bool = False
    uncommitted_count: int = 0
    last_commit_sha: str = ""
    last_commit_msg: str = ""
    last_commit_author: str = ""
    last_commit_time: str = ""
    ahead: int = 0
    behind: int = 0
    stash_count: int = 0
    repo_url: str = ""
    stack: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "branch": self.branch,
            "has_uncommitted": self.has_uncommitted,
            "uncommitted_count": self.uncommitted_count,
            "last_commit_sha": self.last_commit_sha,
            "last_commit_msg": self.last_commit_msg,
            "last_commit_author": self.last_commit_author,
            "last_commit_time": self.last_commit_time,
            "ahead": self.ahead,
            "behind": self.behind,
            "stash_count": self.stash_count,
            "repo_url": self.repo_url,
            "stack": self.stack,
            "error": self.error,
        }

    @property
    def status_emoji(self) -> str:
        if self.error:
            return "❓"
        if self.has_uncommitted:
            return "🟡"
        if self.ahead > 0:
            return "🔵"
        return "🟢"


def _git(path: str, *args: str) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


class ProjectMonitor:
    """Background monitor for all local project git states."""

    def __init__(
        self,
        event_bus: EventBus,
        projects: dict[str, dict],  # project_name -> project_info from registry
        poll_interval: int = 300,
    ):
        self._bus = event_bus
        self._projects = projects
        self._poll_interval = poll_interval
        self._states: dict[str, ProjectState] = {}
        self._running = False

    async def close(self) -> None:
        self._running = False

    @property
    def states(self) -> dict[str, ProjectState]:
        return self._states

    # ── Background scanning ───────────────────────────────────────────────

    async def start_monitoring(self) -> None:
        """Background task: periodically scan all projects."""
        self._running = True
        logger.info("Project monitor started (every %ds, %d projects)", self._poll_interval, len(self._projects))

        # Initial scan
        await self.scan_all(notify=False)

        while self._running:
            await asyncio.sleep(self._poll_interval)
            if not self._running:
                break
            try:
                await self.scan_all(notify=True)
            except Exception as e:
                logger.warning("Project scan failed: %s", e)

    async def scan_all(self, notify: bool = True) -> dict[str, ProjectState]:
        """Scan all projects and return their states."""
        for name, info in self._projects.items():
            path = info.get("path", "")
            if not path or not Path(path).exists():
                continue

            prev_state = self._states.get(name)
            state = await asyncio.to_thread(self._scan_project, name, info)
            self._states[name] = state

            # Detect meaningful changes
            if notify and prev_state and not prev_state.error:
                # New commit
                if state.last_commit_sha and state.last_commit_sha != prev_state.last_commit_sha:
                    await self._bus.emit(
                        EventType.COMMIT,
                        project=name,
                        message=f"New commit: {state.last_commit_msg} by {state.last_commit_author}",
                        source="local",
                        sha=state.last_commit_sha,
                        branch=state.branch,
                    )

                # Branch changed
                if state.branch and state.branch != prev_state.branch:
                    await self._bus.emit(
                        EventType.GIT_STATUS_CHANGE,
                        project=name,
                        message=f"Branch changed: {prev_state.branch} → {state.branch}",
                        source="local",
                        branch=state.branch,
                        prev_branch=prev_state.branch,
                    )

        return self._states

    def _scan_project(self, name: str, info: dict) -> ProjectState:
        """Scan a single project (runs in thread pool)."""
        path = info.get("path", "")
        state = ProjectState(
            name=name,
            path=path,
            repo_url=info.get("repo", ""),
            stack=info.get("stack", ""),
        )

        if not Path(path).joinpath(".git").exists():
            state.error = "Not a git repo"
            return state

        try:
            # Current branch
            state.branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")

            # Uncommitted changes
            status = _git(path, "status", "--porcelain")
            if status:
                state.has_uncommitted = True
                state.uncommitted_count = len(status.strip().splitlines())

            # Last commit
            log_format = "%H%n%s%n%an%n%ar"
            log_output = _git(path, "log", "-1", f"--format={log_format}")
            if log_output:
                parts = log_output.split("\n")
                if len(parts) >= 4:
                    state.last_commit_sha = parts[0][:7]
                    state.last_commit_msg = parts[1]
                    state.last_commit_author = parts[2]
                    state.last_commit_time = parts[3]

            # Ahead/behind
            upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
            if upstream:
                ab = _git(path, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
                if ab and "\t" in ab:
                    ahead, behind = ab.split("\t")
                    state.ahead = int(ahead)
                    state.behind = int(behind)

            # Stash count
            stash = _git(path, "stash", "list")
            if stash:
                state.stash_count = len(stash.strip().splitlines())

        except Exception as e:
            state.error = str(e)

        return state

    # ── Status ────────────────────────────────────────────────────────────

    def status_summary(self) -> dict:
        """Return a summary of all project states."""
        return {
            "total": len(self._states),
            "with_changes": sum(1 for s in self._states.values() if s.has_uncommitted),
            "errors": sum(1 for s in self._states.values() if s.error),
            "running": self._running,
        }

    def all_states(self) -> list[dict]:
        return [s.to_dict() for s in self._states.values()]
