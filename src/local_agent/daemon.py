"""Local agent daemon – runs on ANY PC in the mesh.

Connects to the orchestrator via WebSocket and registers with:
- hostname, OS
- capabilities (claude_code, docker, node, python, etc.)
- project paths available on this machine
- max concurrent tasks

Config via env vars:
    WS_URL          = ws://your-server:8000/ws/agent
    WS_SECRET       = shared-secret
    AGENT_NAME      = my-macbook  (optional, defaults to hostname)
    AGENT_MAX_TASKS = 3
    PROJECTS_DIR    = /Users/you/dev  (auto-discovers projects)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import signal
from pathlib import Path

import websockets

from src.local_agent.process_manager import ProcessManager

logger = logging.getLogger(__name__)


def _load_env() -> None:
    """Load .env file so daemon picks up the same config as the server."""
    try:
        from dotenv import load_dotenv
        # Walk up from cwd or this file to find .env
        for candidate in [Path.cwd(), Path(__file__).resolve().parent.parent.parent]:
            env_file = candidate / ".env"
            if env_file.exists():
                load_dotenv(env_file, override=False)
                logger.info("Loaded config from %s", env_file)
                return
    except ImportError:
        pass


_load_env()

WS_URL = os.environ.get("WS_URL", "ws://localhost:8000/ws/agent")
WS_SECRET = os.environ.get("WS_SECRET", "change-me")
AGENT_NAME = os.environ.get("AGENT_NAME", "")
AGENT_MAX_TASKS = int(os.environ.get("AGENT_MAX_TASKS", "3"))
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "")
RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60


class LocalAgentDaemon:
    """Multi-PC agent daemon with auto-discovery and capability detection."""

    def __init__(
        self,
        ws_url: str = WS_URL,
        ws_secret: str = WS_SECRET,
        agent_name: str = AGENT_NAME,
        max_tasks: int = AGENT_MAX_TASKS,
        projects_dir: str = PROJECTS_DIR,
    ):
        self._ws_url = ws_url
        self._ws_secret = ws_secret
        self._agent_name = agent_name or platform.node()
        self._max_tasks = max_tasks
        self._projects_dir = projects_dir
        self._process_manager = ProcessManager()
        self._running = True
        self._ws = None

    # ── Auto-discovery ────────────────────────────────────────────────────

    def _detect_capabilities(self) -> list[str]:
        """Detect what tools are available on this machine."""
        caps = []
        checks = {
            "claude_code": "claude",
            "node": "node",
            "python": "python3",
            "docker": "docker",
            "git": "git",
            "npm": "npm",
            "pnpm": "pnpm",
            "bun": "bun",
        }
        for cap, binary in checks.items():
            if shutil.which(binary):
                caps.append(cap)
        return caps

    def _discover_projects(self) -> dict[str, str]:
        """Scan PROJECTS_DIR for known projects (dirs with package.json, pyproject.toml, etc.)."""
        projects_dir = self._projects_dir
        if not projects_dir:
            # Try common locations
            home = Path.home()
            for candidate in [home / "dev", home / "projects", home / "code", home / "workspace"]:
                if candidate.is_dir():
                    projects_dir = str(candidate)
                    break

        if not projects_dir or not Path(projects_dir).is_dir():
            return {}

        found = {}
        root = Path(projects_dir)
        markers = {"package.json", "pyproject.toml", "Cargo.toml", "go.mod", "build.gradle"}

        for entry in root.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            # Check if it's a project (has a build file)
            has_marker = any((entry / m).exists() for m in markers)
            if has_marker:
                found[entry.name] = str(entry)

        logger.info("Discovered %d projects in %s", len(found), projects_dir)
        return found

    # ── Main loop ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to the orchestrator and listen for commands."""
        capabilities = self._detect_capabilities()
        projects = self._discover_projects()

        logger.info(
            "Agent '%s' starting | capabilities: %s | projects: %d | connecting to %s",
            self._agent_name, ", ".join(capabilities), len(projects), self._ws_url,
        )

        delay = RECONNECT_DELAY

        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    additional_headers={"Authorization": f"Bearer {self._ws_secret}"},
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    delay = RECONNECT_DELAY

                    # Register with full metadata
                    await ws.send(json.dumps({
                        "type": "hello",
                        "hostname": self._agent_name,
                        "os": f"{platform.system()} {platform.machine()}",
                        "capabilities": capabilities,
                        "projects": projects,
                        "max_concurrent": self._max_tasks,
                    }))

                    logger.info("Connected and registered as '%s'", self._agent_name)

                    async for raw_msg in ws:
                        try:
                            msg = json.loads(raw_msg)
                            await self._handle_message(msg, ws)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON: %s", raw_msg[:200])
                        except Exception:
                            logger.exception("Error handling message")

            except websockets.ConnectionClosed:
                logger.warning("Connection closed, reconnecting in %ds...", delay)
            except ConnectionRefusedError:
                logger.warning("Connection refused, retrying in %ds...", delay)
            except Exception:
                logger.exception("Unexpected error, reconnecting in %ds...", delay)

            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        await self._process_manager.cleanup()
        logger.info("Agent '%s' stopped", self._agent_name)

    # ── Message dispatch ──────────────────────────────────────────────────

    async def _handle_message(self, msg: dict, ws) -> None:
        msg_type = msg.get("type")

        if msg_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))

        elif msg_type == "run_command":
            await self._handle_run_command(msg, ws)

        elif msg_type == "run_claude_code":
            await self._handle_claude_code(msg, ws)

        elif msg_type == "cancel":
            task_id = msg.get("task_id")
            if task_id:
                await self._process_manager.cancel(task_id)
                await ws.send(json.dumps({
                    "type": "result",
                    "task_id": task_id,
                    "success": False,
                    "output": "Cancelado.",
                }))

        else:
            logger.warning("Unknown message type: %s", msg_type)

    async def _handle_run_command(self, msg: dict, ws) -> None:
        task_id = msg.get("task_id", "unknown")
        command = msg.get("command", "")
        cwd = msg.get("cwd", "")
        timeout = msg.get("timeout", 120)

        logger.info("[%s] Running: %s (cwd=%s)", task_id[:8], command, cwd)

        result = await self._process_manager.run(
            command=command, cwd=cwd, timeout=timeout, task_id=task_id,
        )

        await ws.send(json.dumps({
            "type": "result",
            "task_id": task_id,
            "success": result["success"],
            "output": result["output"],
            "exit_code": result.get("exit_code"),
        }))

    async def _handle_claude_code(self, msg: dict, ws) -> None:
        task_id = msg.get("task_id", "unknown")
        prompt = msg.get("prompt", "")
        cwd = msg.get("cwd", "")
        read_only = msg.get("read_only", False)
        timeout = msg.get("timeout", 300)

        allowed_tools = "Read" if read_only else "Read,Edit,Bash"

        # Use proper shell escaping for the prompt
        escaped_prompt = prompt.replace("'", "'\\''")
        command = (
            f"claude -p '{escaped_prompt}' "
            f"--allowedTools '{allowed_tools}' "
            f"--output-format json"
        )

        logger.info("[%s] Claude Code: %s (cwd=%s, ro=%s)", task_id[:8], prompt[:80], cwd, read_only)

        result = await self._process_manager.run(
            command=command, cwd=cwd, timeout=timeout, task_id=task_id,
        )

        output = result["output"]
        try:
            claude_data = json.loads(output)
            output = claude_data.get("result", output)
        except (json.JSONDecodeError, TypeError):
            pass

        await ws.send(json.dumps({
            "type": "result",
            "task_id": task_id,
            "success": result["success"],
            "output": output,
            "exit_code": result.get("exit_code"),
        }))


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    daemon = LocalAgentDaemon()
    loop = asyncio.new_event_loop()

    def _shutdown(sig, frame):
        logger.info("Shutting down...")
        loop.create_task(daemon.stop())

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(daemon.start())
    except KeyboardInterrupt:
        loop.run_until_complete(daemon.stop())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
