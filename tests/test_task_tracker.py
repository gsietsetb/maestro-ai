"""Tests for the task tracker."""

import pytest
import pytest_asyncio
from src.orchestrator.task_tracker import TaskTracker
from src.orchestrator.intent_parser import ParsedIntent


@pytest_asyncio.fixture
async def tracker(tmp_path):
    db_path = tmp_path / "test.db"
    t = TaskTracker(db_path=db_path)
    await t.initialize()
    yield t
    await t.close()


@pytest.mark.asyncio
async def test_create_and_get(tracker):
    intent = ParsedIntent(action="test", project="proj", raw_message="hello")
    await tracker.create("task-1", intent, status="running")
    task = await tracker.get("task-1")
    assert task is not None
    assert task["action"] == "test"
    assert task["project"] == "proj"
    assert task["status"] == "running"


@pytest.mark.asyncio
async def test_complete(tracker):
    intent = ParsedIntent(action="test", raw_message="hi")
    await tracker.create("task-2", intent, status="running")
    await tracker.complete("task-2", success=True, output="done")
    task = await tracker.get("task-2")
    assert task["status"] == "completed"
    assert task["success"] == 1
    assert task["output"] == "done"


@pytest.mark.asyncio
async def test_list_active(tracker):
    intent = ParsedIntent(action="test", raw_message="x")
    await tracker.create("t1", intent, status="running")
    await tracker.create("t2", intent, status="pending")
    await tracker.create("t3", intent, status="completed")
    active = await tracker.list_active()
    ids = [t["id"] for t in active]
    assert "t1" in ids
    assert "t2" in ids
    assert "t3" not in ids


@pytest.mark.asyncio
async def test_update_status(tracker):
    intent = ParsedIntent(action="test", raw_message="x")
    await tracker.create("t4", intent, status="pending")
    await tracker.update_status("t4", "running")
    task = await tracker.get("t4")
    assert task["status"] == "running"


@pytest.mark.asyncio
async def test_list_recent(tracker):
    intent = ParsedIntent(action="test", raw_message="x")
    for i in range(5):
        await tracker.create(f"r{i}", intent, status="completed")
    recent = await tracker.list_recent(limit=3)
    assert len(recent) == 3
