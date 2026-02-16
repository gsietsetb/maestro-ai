"""Agent mesh – manages multiple PC agents with dynamic registration and smart routing.

Each PC/agent registers via WebSocket with:
- hostname, OS, capabilities
- list of project paths available on that machine
- whether Claude Code CLI is installed
- current load (running tasks count)

The mesh routes tasks to the best available agent based on:
1. Which agent has the project on disk
2. Current load (least busy)
3. Capabilities (claude_code, specific tools)
4. Connectivity status (heartbeat)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_TIMEOUT = 90   # consider dead after this


@dataclass
class AgentInfo:
    """Metadata about a connected agent."""

    agent_id: str
    hostname: str
    os_name: str = ""
    ws: WebSocket | None = None
    capabilities: set[str] = field(default_factory=set)  # {"claude_code", "docker", "node", "python"}
    project_paths: dict[str, str] = field(default_factory=dict)  # {project_name: path}
    running_tasks: int = 0
    max_concurrent: int = 3
    last_heartbeat: float = 0.0
    connected_at: float = 0.0

    @property
    def is_alive(self) -> bool:
        return (time.time() - self.last_heartbeat) < HEARTBEAT_TIMEOUT

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent - self.running_tasks)

    @property
    def load_ratio(self) -> float:
        if self.max_concurrent == 0:
            return 1.0
        return self.running_tasks / self.max_concurrent


class AgentMesh:
    """Multi-agent mesh: manages N agents across multiple PCs."""

    def __init__(self, ws_secret: str):
        self._ws_secret = ws_secret
        self._agents: dict[str, AgentInfo] = {}  # agent_id -> AgentInfo
        self._pending: dict[str, asyncio.Future] = {}  # task_id -> Future
        self._heartbeat_task: asyncio.Task | None = None

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def connected_count(self) -> int:
        return sum(1 for a in self._agents.values() if a.is_alive)

    @property
    def total_slots(self) -> int:
        return sum(a.available_slots for a in self._agents.values() if a.is_alive)

    def status_summary(self) -> dict:
        """Return a summary of all agents for the /health endpoint."""
        agents = []
        for a in self._agents.values():
            agents.append({
                "id": a.agent_id[:8],
                "hostname": a.hostname,
                "alive": a.is_alive,
                "load": f"{a.running_tasks}/{a.max_concurrent}",
                "projects": len(a.project_paths),
                "capabilities": sorted(a.capabilities),
            })
        return {
            "connected": self.connected_count,
            "total_agents": len(self._agents),
            "agents": agents,
        }

    # ── WebSocket handling ────────────────────────────────────────────────

    async def handle_agent_connection(self, ws: WebSocket) -> None:
        """Handle an incoming WebSocket connection from any agent."""
        auth = ws.headers.get("authorization", "")
        if auth != f"Bearer {self._ws_secret}":
            await ws.close(code=4001, reason="Unauthorized")
            return

        await ws.accept()
        agent_id = str(uuid.uuid4())
        agent = AgentInfo(
            agent_id=agent_id,
            hostname="unknown",
            ws=ws,
            last_heartbeat=time.time(),
            connected_at=time.time(),
        )
        self._agents[agent_id] = agent

        logger.info("Agent connecting: %s", agent_id[:8])

        # Start heartbeat if not running
        if not self._heartbeat_task or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                    await self._handle_message(agent_id, msg)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from agent %s", agent_id[:8])
                except Exception:
                    logger.exception("Error handling message from %s", agent_id[:8])
        except WebSocketDisconnect:
            logger.warning("Agent %s (%s) disconnected", agent_id[:8], agent.hostname)
        except Exception:
            logger.exception("Error in agent %s WebSocket", agent_id[:8])
        finally:
            self._cleanup_agent(agent_id)

    def _cleanup_agent(self, agent_id: str) -> None:
        """Clean up when an agent disconnects."""
        agent = self._agents.pop(agent_id, None)
        if not agent:
            return

        # Fail any pending tasks for this agent
        for task_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result({
                    "success": False,
                    "output": f"Agente {agent.hostname} se desconecto.",
                })
        logger.info("Agent %s (%s) cleaned up", agent_id[:8], agent.hostname)

    async def _handle_message(self, agent_id: str, msg: dict) -> None:
        """Process messages from agents."""
        agent = self._agents.get(agent_id)
        if not agent:
            return

        msg_type = msg.get("type")
        agent.last_heartbeat = time.time()

        if msg_type == "hello":
            # Agent registration with full metadata
            agent.hostname = msg.get("hostname", "unknown")
            agent.os_name = msg.get("os", "")
            agent.capabilities = set(msg.get("capabilities", []))
            agent.project_paths = msg.get("projects", {})
            agent.max_concurrent = msg.get("max_concurrent", 3)
            logger.info(
                "Agent registered: %s (%s) | %d projects | capabilities: %s",
                agent.hostname, agent_id[:8],
                len(agent.project_paths),
                ", ".join(agent.capabilities) or "basic",
            )

        elif msg_type == "result":
            task_id = msg.get("task_id")
            agent.running_tasks = max(0, agent.running_tasks - 1)
            fut = self._pending.pop(task_id, None)
            if fut and not fut.done():
                fut.set_result(msg)

        elif msg_type == "pong":
            pass

        elif msg_type == "status_update":
            # Agent reports its current state
            agent.running_tasks = msg.get("running_tasks", agent.running_tasks)
            agent.project_paths = msg.get("projects", agent.project_paths)

    # ── Task routing ──────────────────────────────────────────────────────

    def find_best_agent(
        self,
        project_name: str | None = None,
        require_capability: str | None = None,
    ) -> AgentInfo | None:
        """Find the best agent for a task based on project, load, and capabilities.

        Priority:
        1. Agent that has the project on disk + lowest load
        2. Agent with required capability + lowest load
        3. Any alive agent with lowest load
        """
        candidates = [a for a in self._agents.values() if a.is_alive and a.available_slots > 0]
        if not candidates:
            return None

        # Filter by capability if required
        if require_capability:
            cap_agents = [a for a in candidates if require_capability in a.capabilities]
            if cap_agents:
                candidates = cap_agents

        # Prefer agents that have the project
        if project_name:
            project_agents = [a for a in candidates if project_name in a.project_paths]
            if project_agents:
                candidates = project_agents

        # Sort by load (least busy first)
        candidates.sort(key=lambda a: a.load_ratio)
        return candidates[0] if candidates else None

    # ── Command execution ─────────────────────────────────────────────────

    async def run_command(
        self,
        command: str,
        cwd: str = "",
        timeout: int = 120,
        project_name: str | None = None,
    ) -> ExecutionResult:
        """Route a command to the best available agent."""
        agent = self.find_best_agent(project_name=project_name)
        if not agent or not agent.ws:
            return ExecutionResult(
                success=False,
                output=f"No hay agente disponible"
                + (f" con el proyecto '{project_name}'" if project_name else "")
                + ". Ejecuta el daemon en algun PC.",
            )

        # If the agent has a different path for this project, use it
        if project_name and project_name in agent.project_paths:
            cwd = agent.project_paths[project_name]

        task_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[task_id] = fut
        agent.running_tasks += 1

        await agent.ws.send_text(json.dumps({
            "type": "run_command",
            "task_id": task_id,
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
        }))

        try:
            result = await asyncio.wait_for(fut, timeout=timeout + 10)
            output = result.get("output", "")
            return ExecutionResult(
                success=result.get("success", False),
                output=f"[{agent.hostname}] {output}",
            )
        except asyncio.TimeoutError:
            self._pending.pop(task_id, None)
            agent.running_tasks = max(0, agent.running_tasks - 1)
            return ExecutionResult(
                success=False,
                output=f"Timeout ({timeout}s) en agente {agent.hostname}.",
            )

    async def run_claude_code(
        self,
        prompt: str,
        cwd: str = "",
        read_only: bool = False,
        timeout: int = 300,
        project_name: str | None = None,
    ) -> ExecutionResult:
        """Route a Claude Code task to an agent that has claude_code capability."""
        agent = self.find_best_agent(
            project_name=project_name,
            require_capability="claude_code",
        )
        if not agent or not agent.ws:
            return ExecutionResult(
                success=False,
                output="No hay agente con Claude Code disponible.",
            )

        if project_name and project_name in agent.project_paths:
            cwd = agent.project_paths[project_name]

        task_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[task_id] = fut
        agent.running_tasks += 1

        await agent.ws.send_text(json.dumps({
            "type": "run_claude_code",
            "task_id": task_id,
            "prompt": prompt,
            "cwd": cwd,
            "read_only": read_only,
            "timeout": timeout,
        }))

        try:
            result = await asyncio.wait_for(fut, timeout=timeout + 10)
            output = result.get("output", "")
            return ExecutionResult(
                success=result.get("success", False),
                output=f"[{agent.hostname}] {output}",
            )
        except asyncio.TimeoutError:
            self._pending.pop(task_id, None)
            agent.running_tasks = max(0, agent.running_tasks - 1)
            return ExecutionResult(
                success=False,
                output=f"Timeout ({timeout}s) de Claude Code en {agent.hostname}.",
            )

    # ── Heartbeat ─────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Periodically ping agents and clean up dead ones."""
        while self._agents:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            dead = []
            for agent_id, agent in self._agents.items():
                if not agent.is_alive:
                    dead.append(agent_id)
                    continue
                if agent.ws:
                    try:
                        await agent.ws.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        dead.append(agent_id)

            for agent_id in dead:
                logger.warning("Agent %s is dead, removing", agent_id[:8])
                self._cleanup_agent(agent_id)

    # ── Backward compatibility (single-agent API) ─────────────────────────

    @property
    def is_connected(self) -> bool:
        """At least one agent is alive."""
        return self.connected_count > 0
