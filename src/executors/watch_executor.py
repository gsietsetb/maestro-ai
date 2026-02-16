"""Watch executor – sends push notifications and commands to the Apple Watch companion app.

Communication channels:
1. APNs (Apple Push Notification service) for real push notifications
2. HTTP polling endpoint (Watch polls for pending commands)
3. WebSocket (future: real-time bidirectional)

For MVP we use the polling approach:
- Watch app polls GET /api/watch/pending every N seconds
- Orchestrator queues commands in memory/SQLite
- Watch picks them up and executes locally (haptic, display, etc.)
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)


@dataclass
class WatchCommand:
    """A command queued for the Watch to pick up."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "notification"  # notification, haptic, note, alert, run
    title: str = ""
    body: str = ""
    haptic: str = "success"  # success, failure, retry, start, stop, click, directionUp
    data: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    delivered: bool = False


class WatchExecutor:
    """Manages communication with the Apple Watch companion app.

    Queues commands that the Watch picks up via polling.
    Also provides the FastAPI endpoints for the Watch to call.
    """

    def __init__(self, max_queue_size: int = 100):
        self._queue: deque[WatchCommand] = deque(maxlen=max_queue_size)
        self._notes: list[dict] = []  # Quick notes from Watch
        self._max_notes = 500

    def queue_command(self, cmd: WatchCommand) -> str:
        """Add a command to the Watch queue. Returns the command ID."""
        self._queue.append(cmd)
        logger.info("Watch command queued: %s (%s)", cmd.type, cmd.id)
        return cmd.id

    def get_pending(self) -> list[dict]:
        """Get all pending (undelivered) commands for the Watch.

        Marks them as delivered once retrieved.
        """
        pending = []
        for cmd in self._queue:
            if not cmd.delivered:
                cmd.delivered = True
                pending.append({
                    "id": cmd.id,
                    "type": cmd.type,
                    "title": cmd.title,
                    "body": cmd.body,
                    "haptic": cmd.haptic,
                    "data": cmd.data,
                    "created_at": cmd.created_at,
                })
        return pending

    def add_note(self, text: str, source: str = "watch") -> dict:
        """Store a quick note from the Watch."""
        note = {
            "id": str(uuid.uuid4()),
            "text": text,
            "source": source,
            "timestamp": time.time(),
        }
        self._notes.append(note)
        if len(self._notes) > self._max_notes:
            self._notes = self._notes[-self._max_notes:]
        return note

    def get_notes(self, limit: int = 50) -> list[dict]:
        """Get recent notes."""
        return self._notes[-limit:]

    # ── High-level actions (called from slash commands / router) ──────────

    async def send_notification(
        self,
        title: str,
        body: str,
        haptic: str = "success",
    ) -> ExecutionResult:
        """Queue a notification for the Watch."""
        cmd = WatchCommand(
            type="notification",
            title=title,
            body=body,
            haptic=haptic,
        )
        cmd_id = self.queue_command(cmd)
        return ExecutionResult(
            success=True,
            output=f"Notificacion enviada al Watch: {title}\n(id: {cmd_id})",
        )

    async def send_alert(
        self,
        text: str,
        haptic: str = "failure",
    ) -> ExecutionResult:
        """Queue an alert with strong haptic."""
        cmd = WatchCommand(
            type="alert",
            title="Alerta",
            body=text,
            haptic=haptic,
        )
        cmd_id = self.queue_command(cmd)
        return ExecutionResult(
            success=True,
            output=f"Alerta enviada al Watch: {text}\n(haptic: {haptic}, id: {cmd_id})",
        )

    async def save_note(self, text: str) -> ExecutionResult:
        """Save a quick note from/for the Watch."""
        note = self.add_note(text, source="command")
        return ExecutionResult(
            success=True,
            output=f"Nota guardada: {text}\n(id: {note['id']})",
        )

    async def send_task_update(
        self,
        task_id: str,
        status: str,
        summary: str,
    ) -> ExecutionResult:
        """Notify Watch about a task status change."""
        haptic_map = {
            "done": "success",
            "error": "failure",
            "running": "start",
            "queued": "click",
        }
        cmd = WatchCommand(
            type="task_update",
            title=f"Task {status}",
            body=summary[:100],
            haptic=haptic_map.get(status, "click"),
            data={"task_id": task_id, "status": status},
        )
        self.queue_command(cmd)
        return ExecutionResult(
            success=True,
            output=f"Watch notificado: task {task_id} → {status}",
        )

    async def execute_watch(
        self,
        action: str,
        text: Optional[str] = None,
        parameters: Optional[dict] = None,
    ) -> ExecutionResult:
        """Smart dispatch for watch commands from slash parser.

        Actions: note, alert, run, notify
        """
        params = parameters or {}

        if action == "note" and text:
            return await self.save_note(text)

        if action == "alert" and text:
            haptic = params.get("haptic", "failure")
            return await self.send_alert(text, haptic=haptic)

        if action == "notify" and text:
            title = params.get("title", "Orchestrator")
            haptic = params.get("haptic", "success")
            return await self.send_notification(title, text, haptic=haptic)

        if action == "run" and text:
            return ExecutionResult(
                success=True,
                output=f"Comando desde Watch delegado: {text}",
                agent_id="delegate",
            )

        if action == "notes":
            limit = params.get("limit", 20)
            notes = self.get_notes(limit=limit)
            if not notes:
                return ExecutionResult(success=True, output="No hay notas guardadas.")
            lines = ["Notas recientes:"]
            for n in notes:
                lines.append(f"  - {n['text']}")
            return ExecutionResult(success=True, output="\n".join(lines))

        return ExecutionResult(
            success=False,
            output=f"Accion de watch '{action}' no reconocida. "
            "Usa: note, alert, notify, run, notes.",
        )
