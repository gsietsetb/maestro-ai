"""Vercel monitor – polls Vercel API for deployment events.

Also handles Vercel deployment webhooks for real-time notifications.
Maps Vercel projects to local projects via git repo URLs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

import httpx
from fastapi import Request, Response

from src.events import EventBus, EventType

logger = logging.getLogger(__name__)

VERCEL_API = "https://api.vercel.com"


class VercelMonitor:
    """Monitor Vercel deployments via API polling + webhook handler."""

    def __init__(
        self,
        token: str,
        event_bus: EventBus,
        project_repos: dict[str, str],  # project_name -> repo_url (for mapping)
        poll_interval: int = 60,
        team_id: str = "",
    ):
        self._token = token
        self._bus = event_bus
        self._poll_interval = poll_interval
        self._team_id = team_id
        self._project_repos = project_repos
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        # Track known deployments (deployment_id -> status)
        self._known_deployments: dict[str, str] = {}
        # Map Vercel project names to our project names
        self._vercel_to_local: dict[str, str] = {}
        self._running = False
        self._vercel_projects: list[dict] = []

    async def close(self) -> None:
        self._running = False
        await self._client.aclose()

    # ── Initialization ────────────────────────────────────────────────────

    async def _discover_projects(self) -> None:
        """Discover Vercel projects and map them to local projects."""
        params = {}
        if self._team_id:
            params["teamId"] = self._team_id

        try:
            resp = await self._client.get(f"{VERCEL_API}/v9/projects", params=params)
            if resp.status_code != 200:
                logger.warning("Vercel project list failed: %s", resp.status_code)
                return

            data = resp.json()
            self._vercel_projects = data.get("projects", [])

            # Map Vercel projects to local projects via repo URL
            for vp in self._vercel_projects:
                repo_info = vp.get("link", {})
                repo_slug = repo_info.get("org", "") + "/" + repo_info.get("repo", "")
                vp_name = vp.get("name", "")

                for local_name, repo_url in self._project_repos.items():
                    if repo_slug and repo_slug.lower() in repo_url.lower():
                        self._vercel_to_local[vp_name] = local_name
                        break

                # Also try matching by project name
                if vp_name not in self._vercel_to_local:
                    for local_name in self._project_repos:
                        if local_name.lower().replace("-", "") in vp_name.lower().replace("-", ""):
                            self._vercel_to_local[vp_name] = local_name
                            break

            logger.info(
                "Vercel: found %d projects, mapped %d to local",
                len(self._vercel_projects), len(self._vercel_to_local),
            )
        except Exception as e:
            logger.error("Vercel project discovery failed: %s", e)

    # ── Background polling ────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """Background task: poll Vercel for new deployments."""
        self._running = True
        await self._discover_projects()
        logger.info("Vercel polling started (every %ds)", self._poll_interval)

        # Initial poll to set baselines
        await self._poll_deployments(notify=False)

        while self._running:
            await asyncio.sleep(self._poll_interval)
            if not self._running:
                break
            try:
                await self._poll_deployments(notify=True)
            except Exception as e:
                logger.warning("Vercel poll failed: %s", e)

    async def _poll_deployments(self, notify: bool = True) -> None:
        """Poll recent deployments across all projects."""
        params = {"limit": 20, "target": "production"}
        if self._team_id:
            params["teamId"] = self._team_id

        resp = await self._client.get(f"{VERCEL_API}/v6/deployments", params=params)
        if resp.status_code != 200:
            return

        data = resp.json()
        deployments = data.get("deployments", [])

        for dep in deployments:
            dep_id = dep.get("uid", "")
            state = dep.get("state", dep.get("readyState", ""))
            vp_name = dep.get("name", "")
            dep_url = dep.get("url", "")
            created = dep.get("created", 0)

            prev_state = self._known_deployments.get(dep_id)
            self._known_deployments[dep_id] = state

            if prev_state == state or not notify:
                continue

            # Map to local project name
            project_name = self._vercel_to_local.get(vp_name, vp_name)

            # Determine event type
            event_map = {
                "READY": EventType.DEPLOY_SUCCESS,
                "ERROR": EventType.DEPLOY_FAILED,
                "BUILDING": EventType.DEPLOY_STARTED,
                "INITIALIZING": EventType.DEPLOY_STARTED,
                "QUEUED": EventType.DEPLOY_STARTED,
                "CANCELED": EventType.DEPLOY_CANCELLED,
            }
            etype = event_map.get(state)
            if not etype:
                continue

            # Only notify meaningful transitions
            if prev_state is None and state not in ("READY", "ERROR"):
                continue  # Don't notify in-progress on first poll

            meta = dep.get("meta", {})
            commit_msg = meta.get("githubCommitMessage", "")
            commit_author = meta.get("githubCommitAuthorName", "")
            branch = meta.get("githubCommitRef", "")

            msg_parts = [f"Deploy {state.lower()}"]
            if commit_msg:
                msg_parts.append(f": {commit_msg}")
            if commit_author:
                msg_parts.append(f" by {commit_author}")

            await self._bus.emit(
                etype,
                project=project_name,
                message="".join(msg_parts),
                source="vercel",
                url=f"https://{dep_url}" if dep_url else "",
                branch=branch,
                deployment_id=dep_id,
                vercel_project=vp_name,
                commit_message=commit_msg,
                author=commit_author,
            )

        # Cleanup old deployments (keep last 100)
        if len(self._known_deployments) > 100:
            sorted_deps = sorted(self._known_deployments.items())
            self._known_deployments = dict(sorted_deps[-100:])

    # ── Webhook handler ───────────────────────────────────────────────────

    async def handle_webhook(self, request: Request) -> Response:
        """Handle incoming Vercel deployment webhook."""
        payload = await request.json()
        event_type = payload.get("type", "")

        # Vercel webhook types: deployment.created, deployment.succeeded,
        # deployment.ready, deployment.error, deployment.canceled
        dep = payload.get("payload", {}).get("deployment", payload.get("payload", {}))
        vp_name = dep.get("name", payload.get("payload", {}).get("name", ""))
        dep_url = dep.get("url", "")
        state = dep.get("state", dep.get("readyState", ""))

        project_name = self._vercel_to_local.get(vp_name, vp_name)

        meta = dep.get("meta", {})
        commit_msg = meta.get("githubCommitMessage", "")
        commit_author = meta.get("githubCommitAuthorName", "")
        branch = meta.get("githubCommitRef", "")

        etype_map = {
            "deployment.created": EventType.DEPLOY_STARTED,
            "deployment.ready": EventType.DEPLOY_SUCCESS,
            "deployment.succeeded": EventType.DEPLOY_SUCCESS,
            "deployment.error": EventType.DEPLOY_FAILED,
            "deployment.canceled": EventType.DEPLOY_CANCELLED,
        }
        etype = etype_map.get(event_type, EventType.DEPLOY_STARTED)

        msg = f"Deploy {event_type.split('.')[-1]}"
        if commit_msg:
            msg += f": {commit_msg}"

        await self._bus.emit(
            etype, project=project_name,
            message=msg, source="vercel",
            url=f"https://{dep_url}" if dep_url else "",
            branch=branch, author=commit_author,
            commit_message=commit_msg,
        )

        return Response(status_code=200, content="OK")

    # ── Status ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "vercel_projects": len(self._vercel_projects),
            "mapped_projects": self._vercel_to_local,
            "tracked_deployments": len(self._known_deployments),
            "running": self._running,
            "poll_interval": self._poll_interval,
        }

    def recent_deployments(self) -> list[dict]:
        """Return the most recent deployment state for each project."""
        result = []
        for dep_id, state in list(self._known_deployments.items())[-20:]:
            result.append({"id": dep_id, "state": state})
        return result
