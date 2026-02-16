"""Smart action router – dispatches intents to the best executor via the agent mesh.

Routes based on:
- Action type (code_change → Cursor Opus 4.6 or Claude Code, query → Gemini/Claude, etc.)
- Project availability across the agent mesh
- Agent capabilities and current load
- Conversational mode: Gemini for quick chat, Claude Code for deep codebase analysis
- Improvement loop: after code changes, auto-reviews, tests, and improves autonomously
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src.orchestrator.intent_parser import ParsedIntent
from src.orchestrator.project_registry import ProjectRegistry
from src.orchestrator.slash_commands import (
    DELEGATE_SENTINEL,
    CommandResult,
    SlashCommandParser,
)

logger = logging.getLogger(__name__)

# Type alias for progress notification callback
NotifyFn = Callable[[str], Awaitable[None]]


@dataclass
class ExecutionResult:
    """Result of executing a task."""

    success: bool
    output: str
    pr_url: Optional[str] = None
    agent_id: Optional[str] = None


class ActionRouter:
    """Smart router: picks the best executor and agent for each intent.

    Includes an autonomous improvement loop that auto-reviews and improves
    code changes after the initial task completes.
    """

    def __init__(
        self,
        registry: ProjectRegistry,
        cursor_executor=None,
        agent_mesh=None,
        ha_executor=None,
        improvement_loop=None,
        gemini=None,
        slash_parser: SlashCommandParser | None = None,
    ):
        self._registry = registry
        self._cursor = cursor_executor
        self._mesh = agent_mesh  # AgentMesh (replaces single local_executor)
        self._ha = ha_executor   # HomeAssistantExecutor
        self._improver = improvement_loop  # ImprovementLoop
        self._gemini = gemini    # GeminiProvider for conversational fallback
        self._slash = slash_parser  # SlashCommandParser (fast, no LLM)

    async def route(
        self,
        intent: ParsedIntent,
        task_id: str,
        notify: NotifyFn | None = None,
    ) -> ExecutionResult:
        """Route an intent to the correct executor and return the result.

        Args:
            notify: Optional async callback for sending progress updates
                    to the user (used by the improvement loop).
        """
        project_info = None
        project_name = None
        if intent.project:
            project_info = self._registry.resolve(intent.project)
            if not project_info:
                return ExecutionResult(
                    success=False,
                    output=f"Proyecto '{intent.project}' no encontrado.",
                )
            project_name = project_info["_name"]

        action = intent.action

        # ── Conversational: any freeform question ─────────────────────────
        if action in ("query", "plan", "conversation"):
            return await self._handle_conversation(intent, project_info, project_name)

        # ── Code changes: Cursor Opus 4.6 Max or Claude Code ─────────────
        if action == "code_change":
            return await self._handle_code_change(
                intent, project_info, project_name, task_id, notify=notify,
            )

        # ── Operations: tests, lint, build, deploy ────────────────────────
        if action in ("operation", "deploy"):
            return await self._handle_operation(intent, project_info, project_name)

        # ── Git operations ────────────────────────────────────────────────
        if action == "git":
            return await self._handle_git(intent, project_info, project_name)

        # ── Destructive git ───────────────────────────────────────────────
        if action in ("git_force_push", "delete_branch"):
            return await self._handle_git(intent, project_info, project_name)

        # ── Domotica: smart home control ──────────────────────────────────
        if action == "domotica":
            return await self._handle_domotica(intent)

        # ── Unknown → treat as conversation ───────────────────────────────
        return await self._handle_conversation(intent, project_info, project_name)

    # ── Slash commands (fast path, no LLM) ────────────────────────────────

    async def try_slash_command(
        self, message: str, task_id: str
    ) -> ExecutionResult | None:
        """Try to handle the message as a slash command.

        Returns:
            ExecutionResult if handled (including delegate-to-NLU signal).
            None if the message is not a slash command – caller should use
            the Gemini intent parser.
        """
        if not self._slash:
            return None

        cmd_result: CommandResult | None = await self._slash.execute(message)
        if cmd_result is None:
            return None

        # Special case: /run delegates text back to the NLU intent parser
        if cmd_result.status == "delegate":
            return ExecutionResult(
                success=True,
                output=cmd_result.summary,
                agent_id=DELEGATE_SENTINEL,
            )

        return self._command_result_to_execution(cmd_result)

    @staticmethod
    def _command_result_to_execution(cmd: CommandResult) -> ExecutionResult:
        """Convert a slash CommandResult into an ExecutionResult."""
        parts: list[str] = [cmd.summary]

        if cmd.artifacts:
            parts.append("")
            for a in cmd.artifacts:
                if a.get("type") == "link":
                    parts.append(f"  - {a.get('title', '')}: {a.get('url', '')}")
                else:
                    parts.append(f"  - {a}")

        if cmd.next_actions:
            parts.append(f"\n\U0001f4a1 {' | '.join(cmd.next_actions)}")

        return ExecutionResult(
            success=cmd.status != "error",
            output="\n".join(parts),
        )

    # ── Domotica (Home Assistant) ─────────────────────────────────────────

    async def _handle_domotica(self, intent: ParsedIntent) -> ExecutionResult:
        """Route smart home commands to the Home Assistant executor."""
        if not self._ha:
            return ExecutionResult(
                success=False,
                output="Home Assistant no configurado. "
                "Anade HA_URL y HA_TOKEN en .env y arranca HA con docker compose up.",
            )

        ha_action = intent.ha_action or "status"
        return await self._ha.execute_domotica(
            action=ha_action,
            target=intent.ha_target,
            scene=intent.ha_scene,
            parameters=intent.ha_params,
        )

    # ── Conversation / Query (Gemini + Claude Code fallback) ────────────

    async def _handle_conversation(
        self, intent: ParsedIntent, project_info: dict | None, project_name: str | None
    ) -> ExecutionResult:
        """Handle freeform questions and conversations.

        Strategy:
        1. Gemini for quick conversational responses (greeting, simple questions)
        2. Claude Code (via agent mesh) for deep codebase analysis when needed
        3. Graceful fallback if Claude Code is unavailable or fails
        """
        prompt = intent.query_text or intent.prompt or intent.raw_message

        # If it's a plan, prefix with planning instructions
        if intent.action == "plan":
            prompt = (
                f"ONLY PLAN, DO NOT EXECUTE. Analyze the codebase and create a detailed "
                f"step-by-step plan for: {prompt}"
            )

        # For deep codebase questions or plans, try Claude Code first
        needs_deep_analysis = (
            intent.action == "plan"
            or (project_info and any(
                kw in prompt.lower()
                for kw in ["analiza", "analyze", "revisa", "review", "explica el codigo",
                           "refactoriza", "refactor", "debug", "bug", "error", "test",
                           "estructura", "architecture", "como funciona"]
            ))
        )

        if needs_deep_analysis and self._mesh and self._mesh.is_connected:
            try:
                cwd = project_info.get("path", "") if project_info else ""
                result = await self._mesh.run_claude_code(
                    prompt=prompt,
                    cwd=cwd,
                    read_only=True,
                    timeout=180,
                    project_name=project_name,
                )
                # If Claude Code succeeded, return result
                if result.success:
                    return result
                # If it failed due to login or similar, fall through to Gemini
                logger.warning(
                    "Claude Code failed for conversation, falling back to Gemini: %s",
                    result.output[:100],
                )
            except Exception as e:
                logger.warning("Claude Code error, falling back to Gemini: %s", e)

        # Use Gemini for conversational responses (fast, always available)
        if self._gemini and self._gemini.configured:
            system_prompt = (
                "Eres Sierra Bot, el asistente de desarrollo de Guillermo Sierra. "
                "Respondes en espanol de forma directa, util y concisa. "
                "Tienes acceso a proyectos de desarrollo, puedes lanzar tareas de codigo, "
                "monitorizar deploys, controlar la domotica, y mas. "
                "Si te piden algo que requiere analisis profundo del codigo, sugiere "
                "que el usuario especifique el proyecto para un analisis mas detallado."
            )
            if project_info:
                system_prompt += (
                    f"\n\nContexto: El usuario habla sobre el proyecto '{project_name}' "
                    f"ubicado en {project_info.get('path', 'desconocido')}."
                )

            try:
                response = await self._gemini.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=0.7,
                    max_tokens=2048,
                    disable_thinking=True,
                )
                if response:
                    return ExecutionResult(success=True, output=response)
            except Exception as e:
                logger.warning("Gemini conversation failed: %s", e)

        return ExecutionResult(
            success=False,
            output="No pude procesar tu mensaje. Verifica que Gemini o Claude Code esten configurados.",
        )

    # ── Code changes ──────────────────────────────────────────────────────

    async def _handle_code_change(
        self,
        intent: ParsedIntent,
        project_info: dict | None,
        project_name: str | None,
        task_id: str,
        notify: NotifyFn | None = None,
    ) -> ExecutionResult:
        """Route code changes to Cursor (Opus 4.6 Max) or Claude Code CLI.

        Cursor is tried first for GitHub repos, but falls back to Claude Code
        if Cursor fails (401 unauthorized, API errors, etc.)

        After the initial task, the improvement loop auto-reviews, tests,
        and improves the changes autonomously.
        """
        if not project_info:
            return ExecutionResult(success=False, output="Se requiere un proyecto para cambios de codigo.")

        repo = project_info.get("repo", "")
        prompt = intent.prompt or intent.raw_message
        result: ExecutionResult | None = None

        # Strategy 1: Cursor Background Agent (Opus 4.6 Max) for GitHub repos
        if self._cursor and repo and "github.com" in repo:
            result = await self._cursor.launch_agent(
                prompt=prompt,
                repo_url=repo,
                branch=intent.branch,
                task_id=task_id,
                image_data=intent.image_data,
            )
            # If Cursor failed, fall through to Claude Code.
            if not result.success:
                logger.warning(
                    "Cursor agent failed (%s), falling back to Claude Code for %s",
                    result.output[:80], project_name,
                )
                result = None

        # Strategy 2: Claude Code CLI (local) – fallback or primary for non-GitHub repos
        if result is None:
            if self._mesh and self._mesh.is_connected:
                result = await self._mesh.run_claude_code(
                    prompt=prompt,
                    cwd=project_info.get("path", ""),
                    read_only=False,
                    timeout=600,
                    project_name=project_name,
                )
            else:
                return ExecutionResult(
                    success=False,
                    output="No hay ejecutor disponible. Configura CURSOR_API_KEY o conecta un agente local.",
                )

        # ── Improvement loop: auto-review, test, improve ─────────────────
        if result.success and self._improver:
            try:
                result = await self._improver.run(
                    initial_result=result,
                    project_info=project_info,
                    project_name=project_name,
                    notify=notify,
                )
            except Exception as e:
                logger.warning("Improvement loop failed (non-fatal): %s", e)
                # Keep the original result – improvement is best-effort

        return result

    # ── Operations ────────────────────────────────────────────────────────

    async def _handle_operation(
        self, intent: ParsedIntent, project_info: dict | None, project_name: str | None
    ) -> ExecutionResult:
        if not project_info:
            return ExecutionResult(success=False, output="Se requiere un proyecto.")

        if not self._mesh or not self._mesh.is_connected:
            return ExecutionResult(success=False, output="No hay agentes conectados.")

        cmd_key = intent.command or intent.action
        commands = project_info.get("commands", {})
        command = commands.get(cmd_key)

        if not command:
            available = ", ".join(commands.keys()) if commands else "ninguno"
            return ExecutionResult(
                success=False,
                output=f"Comando '{cmd_key}' no definido para {project_info['_name']}. "
                f"Disponibles: {available}",
            )

        return await self._mesh.run_command(
            command=command,
            cwd=project_info.get("path", ""),
            project_name=project_name,
        )

    # ── Git ───────────────────────────────────────────────────────────────

    async def _handle_git(
        self, intent: ParsedIntent, project_info: dict | None, project_name: str | None
    ) -> ExecutionResult:
        if not project_info:
            return ExecutionResult(success=False, output="Se requiere un proyecto para git.")

        if not self._mesh or not self._mesh.is_connected:
            return ExecutionResult(success=False, output="No hay agentes conectados.")

        git_cmd = intent.git_command or "status"
        branch = intent.branch or ""

        full_cmd = f"git {git_cmd}"
        if branch and git_cmd in ("checkout", "push", "pull", "branch -d", "branch -D"):
            full_cmd = f"git {git_cmd} {branch}"

        return await self._mesh.run_command(
            command=full_cmd,
            cwd=project_info.get("path", ""),
            project_name=project_name,
        )
