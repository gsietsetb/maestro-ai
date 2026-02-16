"""Tests for the AgentMesh multi-PC manager."""

import time
import pytest
from src.executors.agent_mesh import AgentMesh, AgentInfo


class TestAgentInfo:
    """Test AgentInfo metadata."""

    def test_is_alive_recent_heartbeat(self):
        agent = AgentInfo(agent_id="a1", hostname="pc1", last_heartbeat=time.time())
        assert agent.is_alive

    def test_is_dead_old_heartbeat(self):
        agent = AgentInfo(agent_id="a1", hostname="pc1", last_heartbeat=time.time() - 200)
        assert not agent.is_alive

    def test_available_slots(self):
        agent = AgentInfo(agent_id="a1", hostname="pc1", max_concurrent=3, running_tasks=1)
        assert agent.available_slots == 2

    def test_no_slots_at_max(self):
        agent = AgentInfo(agent_id="a1", hostname="pc1", max_concurrent=3, running_tasks=3)
        assert agent.available_slots == 0

    def test_load_ratio(self):
        agent = AgentInfo(agent_id="a1", hostname="pc1", max_concurrent=4, running_tasks=2)
        assert agent.load_ratio == 0.5


class TestAgentMesh:
    """Test the AgentMesh routing logic."""

    def _make_mesh(self) -> AgentMesh:
        return AgentMesh(ws_secret="test")

    def _register_agent(
        self,
        mesh: AgentMesh,
        agent_id: str,
        hostname: str,
        capabilities: set | None = None,
        projects: dict | None = None,
        running_tasks: int = 0,
        max_concurrent: int = 3,
    ) -> AgentInfo:
        agent = AgentInfo(
            agent_id=agent_id,
            hostname=hostname,
            capabilities=capabilities or set(),
            project_paths=projects or {},
            running_tasks=running_tasks,
            max_concurrent=max_concurrent,
            last_heartbeat=time.time(),
        )
        mesh._agents[agent_id] = agent
        return agent

    def test_empty_mesh(self):
        mesh = self._make_mesh()
        assert not mesh.is_connected
        assert mesh.connected_count == 0
        assert mesh.find_best_agent() is None

    def test_connected_count(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "pc1")
        self._register_agent(mesh, "a2", "pc2")
        assert mesh.connected_count == 2
        assert mesh.is_connected

    def test_find_any_agent(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "pc1")
        agent = mesh.find_best_agent()
        assert agent is not None
        assert agent.hostname == "pc1"

    def test_find_agent_by_project(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "macbook", projects={"plinng-web": "/Users/guille/dev/plinng-web"})
        self._register_agent(mesh, "a2", "linux-pc", projects={"divenamic": "/home/guille/dev/divenamic"})
        agent = mesh.find_best_agent(project_name="divenamic")
        assert agent.hostname == "linux-pc"

    def test_find_agent_by_capability(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "pc1", capabilities={"git", "node"})
        self._register_agent(mesh, "a2", "pc2", capabilities={"git", "claude_code"})
        agent = mesh.find_best_agent(require_capability="claude_code")
        assert agent.hostname == "pc2"

    def test_prefers_least_loaded(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "busy-pc", running_tasks=2, max_concurrent=3)
        self._register_agent(mesh, "a2", "idle-pc", running_tasks=0, max_concurrent=3)
        agent = mesh.find_best_agent()
        assert agent.hostname == "idle-pc"

    def test_skips_full_agents(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "full-pc", running_tasks=3, max_concurrent=3)
        self._register_agent(mesh, "a2", "has-room", running_tasks=1, max_concurrent=3)
        agent = mesh.find_best_agent()
        assert agent.hostname == "has-room"

    def test_combined_routing(self):
        """Test project + capability + load combined routing."""
        mesh = self._make_mesh()
        self._register_agent(
            mesh, "a1", "macbook",
            capabilities={"claude_code", "git"},
            projects={"plinng-api": "/dev/plinng-api"},
            running_tasks=1,
        )
        self._register_agent(
            mesh, "a2", "linux-server",
            capabilities={"claude_code", "docker"},
            projects={"plinng-api": "/home/dev/plinng-api"},
            running_tasks=0,
        )
        # Should pick linux-server: has project + claude_code + less loaded
        agent = mesh.find_best_agent(
            project_name="plinng-api",
            require_capability="claude_code",
        )
        assert agent.hostname == "linux-server"

    def test_status_summary(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "pc1", capabilities={"git"})
        summary = mesh.status_summary()
        assert summary["connected"] == 1
        assert summary["total_agents"] == 1
        assert len(summary["agents"]) == 1
        assert summary["agents"][0]["hostname"] == "pc1"

    def test_cleanup_removes_agent(self):
        mesh = self._make_mesh()
        self._register_agent(mesh, "a1", "pc1")
        assert mesh.connected_count == 1
        mesh._cleanup_agent("a1")
        assert mesh.connected_count == 0
