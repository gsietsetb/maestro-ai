"""Task tracker – SQLite-backed task state management."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from src.orchestrator.intent_parser import ParsedIntent

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    project TEXT,
    prompt TEXT,
    command TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    success INTEGER,
    output TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    raw_message TEXT
);
"""

CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    event TEXT NOT NULL,
    details TEXT,
    timestamp TEXT NOT NULL
);
"""


class TaskTracker:
    """Async SQLite task tracker with audit log."""

    def __init__(self, db_path: str | Path = "data/orchestrator.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open DB and create tables."""
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(CREATE_TABLE)
        await self._db.execute(CREATE_AUDIT_TABLE)
        await self._db.commit()
        logger.info("Task tracker initialized at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def create(
        self, task_id: str, intent: ParsedIntent, status: str = "pending"
    ) -> None:
        """Create a new task."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO tasks (id, action, project, prompt, command, status, created_at, raw_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                intent.action,
                intent.project,
                intent.prompt,
                intent.command,
                status,
                now,
                intent.raw_message,
            ),
        )
        await self._audit(task_id, "created", f"action={intent.action} project={intent.project}")
        await self._db.commit()

    async def complete(
        self, task_id: str, success: bool, output: str = ""
    ) -> None:
        """Mark a task as completed."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """UPDATE tasks SET status = ?, success = ?, output = ?, completed_at = ?
               WHERE id = ?""",
            ("completed" if success else "failed", int(success), output, now, task_id),
        )
        await self._audit(task_id, "completed", f"success={success}")
        await self._db.commit()

    async def update_status(self, task_id: str, status: str) -> None:
        """Update a task's status."""
        await self._db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        await self._audit(task_id, "status_change", f"status={status}")
        await self._db.commit()

    async def list_active(self) -> list[dict]:
        """Return all non-completed tasks."""
        cursor = await self._db.execute(
            "SELECT * FROM tasks WHERE status NOT IN ('completed', 'failed', 'cancelled') ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get(self, task_id: str) -> Optional[dict]:
        """Get a single task by ID."""
        cursor = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_recent(self, limit: int = 20) -> list[dict]:
        """Return recent tasks."""
        cursor = await self._db.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def _audit(self, task_id: str, event: str, details: str = "") -> None:
        """Write an audit log entry."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO audit_log (task_id, event, details, timestamp) VALUES (?, ?, ?, ?)",
            (task_id, event, details, now),
        )
