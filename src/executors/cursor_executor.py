"""Cursor Cloud Agents API executor.

Launches cloud agents on Cursor's infrastructure to make code changes
and create PRs on GitHub repositories.

Auth: Basic Auth (API key as username, empty password).
API key: Create at https://cursor.com/dashboard?tab=integrations
API docs: https://cursor.com/docs/cloud-agent/api/endpoints
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

import httpx

from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)

CURSOR_API_BASE = "https://api.cursor.com"

# Official agent statuses
STATUS_FINISHED = {"FINISHED"}
STATUS_RUNNING = {"CREATING", "RUNNING"}
STATUS_FAILED = {"FAILED", "ERROR", "CANCELLED"}


class CursorExecutor:
    """Interact with the Cursor Cloud Agents API.

    Uses Basic Auth per the official docs.
    Model selection: leave empty to let Cursor pick the best model automatically.
    """

    # Available models (from /v0/models endpoint)
    MODELS = {
        "opus": "claude-4.6-opus-high-thinking",
        "sonnet": "claude-4.5-sonnet-thinking",
        "composer": "composer-1.5",
        "gpt": "gpt-5.2-high",
        "codex": "gpt-5.3-codex-high",
    }

    def __init__(
        self,
        api_key: str,
        default_model: str | None = "opus",  # Opus by default for max quality
        poll_interval: int = 15,
        max_poll_time: int = 900,  # 15 min
    ):
        self._api_key = api_key
        self._default_model = default_model
        self._poll_interval = poll_interval
        self._max_poll_time = max_poll_time
        # Cursor uses Basic Auth: API key as username, empty password
        basic_token = base64.b64encode(f"{api_key}:".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=CURSOR_API_BASE,
            headers={
                "Authorization": f"Basic {basic_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    @property
    def configured(self) -> bool:
        """True if the API key looks valid (not a placeholder)."""
        return bool(self._api_key) and not self._api_key.startswith("your-")

    async def close(self) -> None:
        await self._client.aclose()

    # ── Verify ─────────────────────────────────────────────────────────────

    async def verify_key(self) -> bool:
        """Quick check that the API key is valid via GET /v0/me."""
        try:
            resp = await self._client.get("/v0/me")
            if resp.status_code == 200:
                data = resp.json()
                logger.info(
                    "Cursor API key verified: %s (%s)",
                    data.get("apiKeyName", "?"),
                    data.get("userEmail", "?"),
                )
                return True
            logger.warning("Cursor API key invalid: HTTP %s", resp.status_code)
            return False
        except Exception as e:
            logger.warning("Could not verify Cursor API key: %s", e)
            return False

    # ── Launch ─────────────────────────────────────────────────────────────

    async def launch_agent(
        self,
        prompt: str,
        repo_url: str,
        branch: Optional[str] = None,
        task_id: Optional[str] = None,
        image_data: Optional[bytes] = None,
        model: Optional[str] = None,
        auto_create_pr: bool = True,
    ) -> ExecutionResult:
        """Launch a Cursor cloud agent.

        Args:
            prompt: The task instruction.
            repo_url: GitHub repository URL.
            branch: Base branch (default: repo default branch).
            model: LLM model name, or None to let Cursor auto-select.
            auto_create_pr: Auto-create a PR when done.
        """
        # Build official payload
        payload: dict = {
            "prompt": self._build_prompt(prompt, image_data),
            "source": {
                "repository": repo_url,
            },
            "target": {
                "autoCreatePr": auto_create_pr,
            },
        }

        # Set model – resolve alias or use raw model name
        effective_model = model or self._default_model
        if effective_model:
            resolved = self.MODELS.get(effective_model, effective_model)
            payload["model"] = resolved

        if branch:
            payload["source"]["ref"] = branch

        logger.info("Launching Cursor agent for %s (task=%s)", repo_url, task_id)

        try:
            resp = await self._client.post("/v0/agents", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Cursor API error: %s %s", e.response.status_code, e.response.text)
            return ExecutionResult(
                success=False,
                output=f"Cursor API error ({e.response.status_code}): {e.response.text[:500]}",
            )
        except Exception as e:
            logger.exception("Failed to launch Cursor agent")
            return ExecutionResult(success=False, output=f"Error lanzando agente: {e}")

        agent_id = data.get("id")
        if not agent_id:
            return ExecutionResult(
                success=False,
                output=f"Respuesta inesperada de Cursor API: {data}",
            )

        agent_url = data.get("target", {}).get("url", "")
        logger.info("Cursor agent launched: %s -> %s", agent_id, agent_url)

        # Poll for completion
        return await self._poll_agent(agent_id, repo_url, agent_url)

    # ── Poll ───────────────────────────────────────────────────────────────

    async def _poll_agent(
        self, agent_id: str, repo_url: str, agent_url: str = ""
    ) -> ExecutionResult:
        """Poll the agent status until it completes or times out."""
        elapsed = 0

        while elapsed < self._max_poll_time:
            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval

            try:
                resp = await self._client.get(f"/v0/agents/{agent_id}")
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning("Error polling agent %s: %s", agent_id, e)
                continue

            status = data.get("status", "UNKNOWN")
            logger.debug("Agent %s status: %s (elapsed=%ds)", agent_id, status, elapsed)

            if status in STATUS_FINISHED:
                target = data.get("target", {})
                pr_url = target.get("prUrl", "")
                summary = data.get("summary", "")
                branch_name = target.get("branchName", "")

                parts = ["Agente completado."]
                if summary:
                    parts.append(f"Resumen: {summary}")
                if pr_url:
                    parts.append(f"PR: {pr_url}")
                if branch_name:
                    parts.append(f"Branch: {branch_name}")
                parts.append(f"Repo: {repo_url}")
                if agent_url:
                    parts.append(f"Ver en Cursor: {agent_url}")

                return ExecutionResult(
                    success=True,
                    output="\n".join(parts),
                    pr_url=pr_url,
                    agent_id=agent_id,
                )

            if status in STATUS_FAILED:
                summary = data.get("summary", "Sin detalle")
                return ExecutionResult(
                    success=False,
                    output=f"Agente fallo ({status}): {summary}\nAgent ID: {agent_id}",
                    agent_id=agent_id,
                )

        # Timeout
        return ExecutionResult(
            success=False,
            output=(
                f"Timeout esperando al agente ({self._max_poll_time}s). "
                f"Agent ID: {agent_id}. Verificalo en: {agent_url or 'cursor.com/agents'}"
            ),
            agent_id=agent_id,
        )

    # ── Follow-up ──────────────────────────────────────────────────────────

    async def add_followup(self, agent_id: str, message: str) -> ExecutionResult:
        """Send a follow-up instruction to a running/stopped agent."""
        try:
            resp = await self._client.post(
                f"/v0/agents/{agent_id}/followup",
                json={"prompt": {"text": message}},
            )
            resp.raise_for_status()
            return ExecutionResult(success=True, output=f"Follow-up enviado al agente {agent_id}.")
        except Exception as e:
            return ExecutionResult(success=False, output=f"Error enviando follow-up: {e}")

    # ── Stop ───────────────────────────────────────────────────────────────

    async def stop_agent(self, agent_id: str) -> ExecutionResult:
        """Stop a running cloud agent."""
        try:
            resp = await self._client.post(f"/v0/agents/{agent_id}/stop")
            resp.raise_for_status()
            return ExecutionResult(success=True, output=f"Agente {agent_id} detenido.")
        except Exception as e:
            return ExecutionResult(success=False, output=f"Error deteniendo agente: {e}")

    # ── List ───────────────────────────────────────────────────────────────

    async def list_agents(self, limit: int = 10) -> ExecutionResult:
        """List recent cloud agents."""
        try:
            resp = await self._client.get("/v0/agents", params={"limit": limit})
            resp.raise_for_status()
            data = resp.json()
            agent_list = data.get("agents", [])

            if not agent_list:
                return ExecutionResult(success=True, output="No hay agentes recientes.")

            lines = ["Agentes de Cursor:\n"]
            for agent in agent_list:
                aid = agent.get("id", "?")
                status = agent.get("status", "?")
                name = agent.get("name", "")
                repo = agent.get("source", {}).get("repository", "?")
                pr = agent.get("target", {}).get("prUrl", "")
                line = f"  {aid} | {status} | {name}"
                if pr:
                    line += f" | PR: {pr}"
                lines.append(line)

            return ExecutionResult(success=True, output="\n".join(lines))
        except Exception as e:
            return ExecutionResult(success=False, output=f"Error listando agentes: {e}")

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(text: str, image_data: Optional[bytes] = None) -> dict:
        """Build the prompt payload per the official API spec."""
        prompt: dict = {"text": text}
        if image_data:
            prompt["images"] = [
                {
                    "data": base64.b64encode(image_data).decode("utf-8"),
                }
            ]
        return prompt
