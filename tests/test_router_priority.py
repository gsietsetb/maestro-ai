"""Router priority tests: Codex > Claude > Cursor."""

from __future__ import annotations

import pytest

from src.orchestrator.intent_parser import ParsedIntent
from src.orchestrator.router import ActionRouter, ExecutionResult


class DummyRegistry:
    def __init__(self, projects: dict[str, dict]):
        self._projects = projects

    def resolve(self, name: str):
        info = self._projects.get(name)
        if not info:
            return None
        return {"_name": name, **info}


class MeshStub:
    def __init__(
        self,
        codex_result: ExecutionResult | None = None,
        claude_result: ExecutionResult | None = None,
    ):
        self.is_connected = True
        self._codex_result = codex_result or ExecutionResult(False, "no codex")
        self._claude_result = claude_result or ExecutionResult(False, "no claude")
        self.calls: list[str] = []

    async def run_codex_code(self, **kwargs):
        self.calls.append("codex")
        return self._codex_result

    async def run_claude_code(self, **kwargs):
        self.calls.append("claude")
        return self._claude_result


class CursorStub:
    def __init__(self, result: ExecutionResult):
        self._result = result
        self.calls = 0

    async def launch_agent(self, **kwargs):
        self.calls += 1
        return self._result


@pytest.mark.asyncio
async def test_code_change_prefers_codex_then_stops():
    registry = DummyRegistry({
        "demo": {"path": "/tmp/demo", "repo": "https://github.com/acme/demo"},
    })
    mesh = MeshStub(
        codex_result=ExecutionResult(True, "codex ok"),
        claude_result=ExecutionResult(True, "claude ok"),
    )
    cursor = CursorStub(ExecutionResult(True, "cursor ok"))
    router = ActionRouter(registry=registry, agent_mesh=mesh, cursor_executor=cursor)

    result = await router.route(
        ParsedIntent(action="code_change", project="demo", prompt="fix bug"),
        task_id="t1",
    )

    assert result.success
    assert result.output == "codex ok"
    assert mesh.calls == ["codex"]
    assert cursor.calls == 0


@pytest.mark.asyncio
async def test_code_change_falls_back_to_claude_before_cursor():
    registry = DummyRegistry({
        "demo": {"path": "/tmp/demo", "repo": "https://github.com/acme/demo"},
    })
    mesh = MeshStub(
        codex_result=ExecutionResult(False, "codex fail"),
        claude_result=ExecutionResult(True, "claude ok"),
    )
    cursor = CursorStub(ExecutionResult(True, "cursor ok"))
    router = ActionRouter(registry=registry, agent_mesh=mesh, cursor_executor=cursor)

    result = await router.route(
        ParsedIntent(action="code_change", project="demo", prompt="fix bug"),
        task_id="t2",
    )

    assert result.success
    assert result.output == "claude ok"
    assert mesh.calls == ["codex", "claude"]
    assert cursor.calls == 0


@pytest.mark.asyncio
async def test_code_change_uses_cursor_only_after_local_failures():
    registry = DummyRegistry({
        "demo": {"path": "/tmp/demo", "repo": "https://github.com/acme/demo"},
    })
    mesh = MeshStub(
        codex_result=ExecutionResult(False, "codex fail"),
        claude_result=ExecutionResult(False, "claude fail"),
    )
    cursor = CursorStub(ExecutionResult(True, "cursor ok"))
    router = ActionRouter(registry=registry, agent_mesh=mesh, cursor_executor=cursor)

    result = await router.route(
        ParsedIntent(action="code_change", project="demo", prompt="fix bug"),
        task_id="t3",
    )

    assert result.success
    assert result.output == "cursor ok"
    assert mesh.calls == ["codex", "claude"]
    assert cursor.calls == 1
