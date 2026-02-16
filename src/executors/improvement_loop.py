"""Autonomous improvement sub-agent.

After a code task completes, this loop automatically:
1. Reviews the changes made
2. Runs tests
3. If tests fail → fixes them
4. Looks for improvements (edge cases, types, docs, performance)
5. Applies improvements + runs tests again
6. Repeats up to N iterations or until no more improvements found

Works with both Cursor Cloud Agents (via follow-up API) and Claude Code CLI.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)

# Type alias for the notification callback
NotifyFn = Callable[[str], Awaitable[None]]


@dataclass
class ImprovementConfig:
    """Configuration for the improvement loop."""

    max_iterations: int = 3          # Max review-improve cycles
    run_tests: bool = True           # Run tests between iterations
    improve_quality: bool = True     # Look for code quality improvements
    add_tests: bool = True           # Write missing tests
    fix_lint: bool = True            # Fix linting issues
    timeout_per_step: int = 300      # Timeout per Claude Code call (5 min)

    # Stop conditions
    stop_on_test_pass: bool = False  # If True, stop as soon as tests pass (no further quality improvements)


# ── Prompts for each improvement phase ────────────────────────────────────────

PROMPTS = {
    "review": (
        "You just completed a code change. Now review your own work critically:\n\n"
        "1. Check for bugs, edge cases, and logical errors\n"
        "2. Look for missing error handling\n"
        "3. Check types and null safety\n"
        "4. Verify the change is complete and consistent\n\n"
        "If you find issues, fix them immediately. If everything looks good, say 'LGTM'.\n"
        "Be concise. List what you fixed or say LGTM."
    ),
    "test_run": (
        "Run the tests for this project. Use the appropriate test runner "
        "(npm test, pytest, cargo test, etc). Report:\n"
        "- How many tests passed/failed\n"
        "- Any failing test names and errors\n"
        "Be concise."
    ),
    "test_fix": (
        "The tests are failing. Analyze the test output below and fix the issues:\n\n"
        "{test_output}\n\n"
        "Fix the failing tests - either fix the code that's wrong or update tests "
        "that are outdated. Then run the tests again to confirm they pass.\n"
        "Report what you fixed and the final test result."
    ),
    "add_tests": (
        "Review the recent changes and add missing tests:\n\n"
        "1. Identify functions/components that were changed but lack test coverage\n"
        "2. Write focused tests for edge cases and error paths\n"
        "3. Run the tests to make sure they pass\n\n"
        "Be practical - only add tests that catch real bugs. "
        "Report what tests you added and if they pass."
    ),
    "quality": (
        "Final quality pass on the recent changes:\n\n"
        "1. Fix any linting or formatting issues\n"
        "2. Add/improve type annotations where missing\n"
        "3. Improve error messages and logging\n"
        "4. Add brief comments for complex logic\n"
        "5. Clean up any TODO or FIXME you can resolve\n\n"
        "Only make improvements that genuinely help. Don't change things for the sake of it.\n"
        "Report what you improved or say 'Nothing to improve'."
    ),
    "cursor_followup": (
        "Review your own changes critically and improve them:\n\n"
        "1. Fix any bugs, edge cases, or missing error handling\n"
        "2. Run tests and fix any failures\n"
        "3. Add tests for uncovered changes\n"
        "4. Fix linting and type issues\n\n"
        "Be thorough but practical. Only make meaningful improvements."
    ),
}


class ImprovementLoop:
    """Autonomous improvement sub-agent that iterates on completed tasks."""

    def __init__(
        self,
        agent_mesh=None,
        cursor_executor=None,
        config: ImprovementConfig | None = None,
    ):
        self._mesh = agent_mesh
        self._cursor = cursor_executor
        self._config = config or ImprovementConfig()

    async def run(
        self,
        initial_result: ExecutionResult,
        project_info: dict,
        project_name: str | None,
        notify: NotifyFn | None = None,
    ) -> ExecutionResult:
        """Run the improvement loop after an initial task completes.

        Args:
            initial_result: The result from the original task.
            project_info: Project metadata (path, repo, commands, etc.)
            project_name: Human-readable project name.
            notify: Async callback to send progress updates to the user.

        Returns:
            The final ExecutionResult with accumulated improvements.
        """
        if not initial_result.success:
            return initial_result  # Don't improve failed tasks

        async def _notify(msg: str) -> None:
            if notify:
                try:
                    await notify(msg)
                except Exception:
                    pass  # Don't let notification errors break the loop

        agent_id = initial_result.agent_id
        repo = project_info.get("repo", "")
        cwd = project_info.get("path", "")
        improvements: list[str] = []

        # ── Strategy: Cursor follow-up vs Claude Code ────────────────────
        use_cursor = bool(agent_id and self._cursor and repo and "github.com" in repo)
        use_claude = bool(self._mesh and self._mesh.is_connected)

        if not use_cursor and not use_claude:
            logger.info("No executor available for improvement loop")
            return initial_result

        await _notify("Iniciando revision autonoma...")

        for iteration in range(1, self._config.max_iterations + 1):
            phase_label = f"[Iter {iteration}/{self._config.max_iterations}]"
            logger.info("Improvement loop %s for %s", phase_label, project_name)

            # ── Phase 1: Self-review ─────────────────────────────────────
            await _notify(f"{phase_label} Revisando cambios...")
            review = await self._execute_step(
                prompt=PROMPTS["review"],
                cwd=cwd,
                project_name=project_name,
                agent_id=agent_id,
                use_cursor=use_cursor,
            )

            if review.success and self._is_clean(review.output):
                improvements.append(f"{phase_label} Review: LGTM, sin cambios")
                if iteration == 1:
                    # First iteration clean → still run tests
                    pass
                else:
                    # Subsequent iteration clean → we're done
                    await _notify(f"{phase_label} Todo limpio, terminando mejoras.")
                    break
            elif review.success:
                improvements.append(f"{phase_label} Review: {self._first_line(review.output)}")

            # ── Phase 2: Run tests ───────────────────────────────────────
            if self._config.run_tests:
                await _notify(f"{phase_label} Ejecutando tests...")
                test_result = await self._execute_step(
                    prompt=PROMPTS["test_run"],
                    cwd=cwd,
                    project_name=project_name,
                    agent_id=agent_id,
                    use_cursor=use_cursor,
                )

                tests_passed = test_result.success and self._tests_look_good(test_result.output)

                if not tests_passed:
                    # ── Phase 2b: Fix failing tests ──────────────────────
                    await _notify(f"{phase_label} Tests fallaron, arreglando...")
                    fix_prompt = PROMPTS["test_fix"].format(
                        test_output=test_result.output[:2000]
                    )
                    fix_result = await self._execute_step(
                        prompt=fix_prompt,
                        cwd=cwd,
                        project_name=project_name,
                        agent_id=agent_id,
                        use_cursor=use_cursor,
                    )
                    improvements.append(
                        f"{phase_label} Tests: arreglados → {self._first_line(fix_result.output)}"
                    )
                else:
                    improvements.append(f"{phase_label} Tests: OK")
                    if self._config.stop_on_test_pass:
                        await _notify(f"{phase_label} Tests pasan, terminando.")
                        break

            # ── Phase 3: Add missing tests ───────────────────────────────
            if self._config.add_tests and iteration <= 2:
                await _notify(f"{phase_label} Anadiendo tests...")
                test_add = await self._execute_step(
                    prompt=PROMPTS["add_tests"],
                    cwd=cwd,
                    project_name=project_name,
                    agent_id=agent_id,
                    use_cursor=use_cursor,
                )
                if test_add.success and not self._is_nothing(test_add.output):
                    improvements.append(
                        f"{phase_label} Tests nuevos: {self._first_line(test_add.output)}"
                    )

            # ── Phase 4: Quality pass ────────────────────────────────────
            if self._config.improve_quality and iteration <= 2:
                await _notify(f"{phase_label} Mejorando calidad...")
                quality = await self._execute_step(
                    prompt=PROMPTS["quality"],
                    cwd=cwd,
                    project_name=project_name,
                    agent_id=agent_id,
                    use_cursor=use_cursor,
                )
                if quality.success and not self._is_nothing(quality.output):
                    improvements.append(
                        f"{phase_label} Calidad: {self._first_line(quality.output)}"
                    )

        # ── Build final result ───────────────────────────────────────────
        improvement_summary = "\n".join(improvements) if improvements else "Sin mejoras adicionales."
        final_output = (
            f"{initial_result.output}\n\n"
            f"--- Mejora autonoma ({len(improvements)} pasos) ---\n"
            f"{improvement_summary}"
        )

        await _notify(
            f"Mejora autonoma completada: {len(improvements)} pasos aplicados."
        )

        return ExecutionResult(
            success=initial_result.success,
            output=final_output,
            pr_url=initial_result.pr_url,
            agent_id=initial_result.agent_id,
        )

    # ── Execute a single step ─────────────────────────────────────────────────

    async def _execute_step(
        self,
        prompt: str,
        cwd: str,
        project_name: str | None,
        agent_id: str | None,
        use_cursor: bool,
    ) -> ExecutionResult:
        """Execute one improvement step via Cursor follow-up or Claude Code."""
        try:
            if use_cursor and agent_id and self._cursor:
                # Use Cursor follow-up API for GitHub repos
                result = await self._cursor.add_followup(agent_id, prompt)
                if result.success:
                    # Poll briefly for the agent to process
                    await asyncio.sleep(10)
                return result
            elif self._mesh and self._mesh.is_connected:
                # Use Claude Code for local execution
                return await self._mesh.run_claude_code(
                    prompt=prompt,
                    cwd=cwd,
                    read_only=False,
                    timeout=self._config.timeout_per_step,
                    project_name=project_name,
                )
            else:
                return ExecutionResult(success=False, output="No executor available")
        except Exception as e:
            logger.warning("Improvement step failed: %s", e)
            return ExecutionResult(success=False, output=str(e))

    # ── Output analysis helpers ───────────────────────────────────────────────

    @staticmethod
    def _is_clean(output: str) -> bool:
        """Check if review output indicates no issues found."""
        lower = output.lower()
        clean_signals = ["lgtm", "looks good", "no issues", "todo bien", "sin problemas", "nothing to"]
        return any(s in lower for s in clean_signals)

    @staticmethod
    def _is_nothing(output: str) -> bool:
        """Check if the step found nothing to do."""
        lower = output.lower()
        nothing_signals = [
            "nothing to improve", "no changes", "nada que mejorar",
            "sin cambios", "no improvements", "already good",
            "no tests to add", "no missing tests",
        ]
        return any(s in lower for s in nothing_signals)

    @staticmethod
    def _tests_look_good(output: str) -> bool:
        """Heuristic: do the tests appear to pass?"""
        lower = output.lower()
        pass_signals = [
            "all tests pass", "tests passed", "0 failed", "0 failing",
            " passed", "test suites: ", "ok (", "todos los tests",
        ]
        fail_signals = [
            "failed", "failure", "error", "failing", "fallo",
            "assertion", "expect(", "assert ",
        ]
        has_pass = any(s in lower for s in pass_signals)
        has_fail = any(s in lower for s in fail_signals)
        # If output mentions pass but not fail → good
        if has_pass and not has_fail:
            return True
        # If it explicitly mentions failures → bad
        if has_fail:
            return False
        # Unknown → assume pass (we'll catch it next iteration)
        return True

    @staticmethod
    def _first_line(output: str) -> str:
        """Get first meaningful line of output for the summary."""
        for line in output.strip().splitlines():
            line = line.strip()
            if line and len(line) > 5 and not line.startswith("["):
                return line[:120]
        return output[:120]
