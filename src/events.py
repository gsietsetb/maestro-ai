"""Central event system – all project events flow through here.

Events from: GitHub (push, PR), Vercel (deploy), Cursor (task), local git changes.
Events to: Telegram, WhatsApp, Dashboard, SQLite log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# Type for event subscribers
EventCallback = Callable[["Event"], Awaitable[None]]


class EventType(str, Enum):
    """All event types the system tracks."""

    # Git / GitHub
    PUSH = "push"
    PR_OPENED = "pr_opened"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    PR_REVIEW = "pr_review"
    COMMIT = "commit"
    BRANCH_CREATED = "branch_created"
    BRANCH_DELETED = "branch_deleted"

    # Vercel
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILED = "deploy_failed"
    DEPLOY_CANCELLED = "deploy_cancelled"

    # Cursor / Tasks
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"

    # Local
    GIT_STATUS_CHANGE = "git_status_change"
    PROJECT_ERROR = "project_error"

    # System
    BOT_STARTED = "bot_started"
    MONITOR_ERROR = "monitor_error"


# Events that trigger proactive notifications
NOTIFY_EVENTS = {
    EventType.PUSH,
    EventType.PR_OPENED,
    EventType.PR_MERGED,
    EventType.DEPLOY_STARTED,
    EventType.DEPLOY_SUCCESS,
    EventType.DEPLOY_FAILED,
    EventType.TASK_COMPLETED,
    EventType.TASK_FAILED,
}


@dataclass
class Event:
    """A single event in the system."""

    type: EventType
    project: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # "github", "vercel", "cursor", "local", "bot"

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def icon(self) -> str:
        icons = {
            EventType.PUSH: "📤",
            EventType.PR_OPENED: "🔀",
            EventType.PR_MERGED: "✅",
            EventType.PR_CLOSED: "❌",
            EventType.DEPLOY_STARTED: "🚀",
            EventType.DEPLOY_SUCCESS: "🟢",
            EventType.DEPLOY_FAILED: "🔴",
            EventType.DEPLOY_CANCELLED: "⚪",
            EventType.TASK_STARTED: "⚙️",
            EventType.TASK_COMPLETED: "✨",
            EventType.TASK_FAILED: "💥",
            EventType.GIT_STATUS_CHANGE: "📝",
            EventType.COMMIT: "💾",
            EventType.BOT_STARTED: "🤖",
        }
        return icons.get(self.type, "📌")

    @property
    def should_notify(self) -> bool:
        return self.type in NOTIFY_EVENTS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["icon"] = self.icon
        d["dt"] = self.dt.isoformat()
        return d

    def format_notification(self) -> str:
        """Format for Telegram/WhatsApp notification."""
        time_str = self.dt.strftime("%H:%M")
        proj = self.project or "global"
        lines = [f"{self.icon} *{proj}* — {self.type.value.replace('_', ' ').title()}"]
        lines.append(self.message)
        if self.metadata.get("url"):
            lines.append(f"🔗 {self.metadata['url']}")
        if self.metadata.get("branch"):
            lines.append(f"🌿 Branch: {self.metadata['branch']}")
        lines.append(f"⏰ {time_str}")
        return "\n".join(lines)


class EventStore:
    """Persistent event store (SQLite) + in-memory recent cache."""

    def __init__(self, db_path: str = "data/events.db", max_memory: int = 500):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_memory = max_memory
        self._recent: list[Event] = []
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                project TEXT,
                message TEXT,
                metadata TEXT,
                source TEXT,
                timestamp REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_project ON events(project)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC)
        """)
        await self._db.commit()

        # Load recent events into memory
        async with self._db.execute(
            "SELECT type, project, message, metadata, source, timestamp "
            "FROM events ORDER BY timestamp DESC LIMIT ?",
            (self._max_memory,),
        ) as cursor:
            rows = await cursor.fetchall()
            for row in reversed(rows):
                self._recent.append(Event(
                    type=EventType(row[0]),
                    project=row[1] or "",
                    message=row[2] or "",
                    metadata=json.loads(row[3]) if row[3] else {},
                    source=row[4] or "",
                    timestamp=row[5],
                ))
        logger.info("Event store initialized: %d events loaded", len(self._recent))

    async def add(self, event: Event) -> None:
        """Store event and add to memory cache."""
        self._recent.append(event)
        if len(self._recent) > self._max_memory:
            self._recent = self._recent[-self._max_memory:]

        if self._db:
            await self._db.execute(
                "INSERT INTO events (type, project, message, metadata, source, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.type.value,
                    event.project,
                    event.message,
                    json.dumps(event.metadata),
                    event.source,
                    event.timestamp,
                ),
            )
            await self._db.commit()

    def recent(self, limit: int = 50, project: str | None = None) -> list[Event]:
        events = self._recent
        if project:
            events = [e for e in events if e.project == project]
        return events[-limit:]

    def by_type(self, event_type: EventType, limit: int = 20) -> list[Event]:
        return [e for e in self._recent if e.type == event_type][-limit:]

    async def close(self) -> None:
        if self._db:
            await self._db.close()


class EventBus:
    """Publish-subscribe event bus. All events flow through here."""

    def __init__(self, store: EventStore):
        self._store = store
        self._subscribers: list[EventCallback] = []
        self._lock = asyncio.Lock()

    @property
    def store(self) -> EventStore:
        return self._store

    def subscribe(self, callback: EventCallback) -> None:
        self._subscribers.append(callback)

    async def publish(self, event: Event) -> None:
        """Store event and notify all subscribers."""
        await self._store.add(event)
        logger.info(
            "Event: %s | %s | %s",
            event.type.value, event.project, event.message[:80],
        )
        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception as e:
                logger.error("Event subscriber error: %s", e)

    async def emit(
        self,
        event_type: EventType,
        project: str,
        message: str,
        source: str = "",
        **metadata,
    ) -> Event:
        """Convenience: create and publish an event."""
        event = Event(
            type=event_type,
            project=project,
            message=message,
            metadata=metadata,
            source=source,
        )
        await self.publish(event)
        return event
