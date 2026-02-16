"""Backward-compatible alias – use AgentMesh instead.

This module exists so old imports don't break.
AgentMesh replaces LocalExecutor with multi-PC support.
"""

from src.executors.agent_mesh import AgentMesh as LocalExecutor  # noqa: F401

__all__ = ["LocalExecutor"]
