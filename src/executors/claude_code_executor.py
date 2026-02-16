"""Claude Code CLI executor – routes through the agent mesh.

Uses Claude Code in headless mode (`claude -p`) for AI-powered tasks.
Since Claude Code + Cursor are paid by the company, we can use them heavily.
"""

from __future__ import annotations

import logging

from src.executors.agent_mesh import AgentMesh
from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)


class ClaudeCodeExecutor:
    """Execute coding tasks via Claude Code CLI through the agent mesh."""

    def __init__(self, agent_mesh: AgentMesh):
        self._mesh = agent_mesh

    @property
    def is_available(self) -> bool:
        """Check if any agent with Claude Code is connected."""
        agent = self._mesh.find_best_agent(require_capability="claude_code")
        return agent is not None

    async def execute(
        self,
        prompt: str,
        project_path: str = "",
        project_name: str | None = None,
        task_id: str = "claude-code",
        read_only: bool = False,
        timeout: int = 300,
    ) -> ExecutionResult:
        """Execute a Claude Code task on the best available agent."""
        return await self._mesh.run_claude_code(
            prompt=prompt,
            cwd=project_path,
            read_only=read_only,
            timeout=timeout,
            project_name=project_name,
        )

    async def analyze_project(self, project_path: str, project_name: str | None = None) -> ExecutionResult:
        """Quick read-only project analysis."""
        return await self.execute(
            prompt=(
                "Analyze this project briefly: "
                "1) What it does, 2) Tech stack, 3) Key files, 4) Any issues."
            ),
            project_path=project_path,
            project_name=project_name,
            read_only=True,
            timeout=120,
        )

    async def fix_and_test(self, prompt: str, project_path: str, project_name: str | None = None) -> ExecutionResult:
        """Make changes and run tests."""
        return await self.execute(
            prompt=f"{prompt}\n\nAfter changes, run the test suite and report results.",
            project_path=project_path,
            project_name=project_name,
            read_only=False,
            timeout=600,
        )
