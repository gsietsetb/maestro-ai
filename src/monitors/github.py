"""GitHub monitor – polls GitHub API for push, PR, and deployment events.

Also handles incoming GitHub webhooks for real-time notifications.
Extracts repo names from projects.yaml to know which repos to watch.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from typing import Any, Optional

import httpx
from fastapi import Request, Response

from src.events import EventBus, EventType

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _extract_owner_repo(repo_url: str) -> str | None:
    """Extract 'owner/repo' from a GitHub URL."""
    m = re.match(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?$", repo_url)
    return m.group(1) if m else None


class GitHubMonitor:
    """Monitor GitHub repos for events via API polling + webhook handler."""

    def __init__(
        self,
        token: str,
        event_bus: EventBus,
        repos: dict[str, str],  # project_name -> repo_url
        poll_interval: int = 60,
        webhook_secret: str = "",
    ):
        self._token = token
        self._bus = event_bus
        self._poll_interval = poll_interval
        self._webhook_secret = webhook_secret
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        # Map project_name -> owner/repo
        self._repos: dict[str, str] = {}
        for name, url in repos.items():
            owner_repo = _extract_owner_repo(url)
            if owner_repo:
                self._repos[name] = owner_repo

        # Track last seen event timestamps per repo
        self._last_seen: dict[str, str] = {}  # owner/repo -> last event id/etag
        self._last_push_sha: dict[str, str] = {}  # owner/repo -> last push sha
        self._running = False

        logger.info("GitHub monitor: tracking %d repos", len(self._repos))

    async def close(self) -> None:
        self._running = False
        await self._client.aclose()

    # ── Background polling ────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """Background task: poll all repos for new events."""
        self._running = True
        logger.info("GitHub polling started (every %ds)", self._poll_interval)

        # Initial poll to set baselines (don't notify on startup)
        for project_name, owner_repo in self._repos.items():
            try:
                await self._poll_repo(project_name, owner_repo, notify=False)
            except Exception as e:
                logger.warning("GitHub initial poll failed for %s: %s", owner_repo, e)

        while self._running:
            await asyncio.sleep(self._poll_interval)
            for project_name, owner_repo in self._repos.items():
                if not self._running:
                    break
                try:
                    await self._poll_repo(project_name, owner_repo, notify=True)
                except Exception as e:
                    logger.warning("GitHub poll failed for %s: %s", owner_repo, e)

    async def _poll_repo(self, project_name: str, owner_repo: str, notify: bool = True) -> None:
        """Poll a single repo for new events."""
        # Check recent pushes (commits on default branch)
        resp = await self._client.get(
            f"{GITHUB_API}/repos/{owner_repo}/commits",
            params={"per_page": 5},
        )
        if resp.status_code != 200:
            return

        commits = resp.json()
        if not commits:
            return

        latest_sha = commits[0]["sha"]
        prev_sha = self._last_push_sha.get(owner_repo)
        self._last_push_sha[owner_repo] = latest_sha

        if prev_sha and latest_sha != prev_sha and notify:
            # New commit(s) pushed
            new_commits = []
            for c in commits:
                if c["sha"] == prev_sha:
                    break
                new_commits.append(c)

            if new_commits:
                count = len(new_commits)
                author = new_commits[0].get("commit", {}).get("author", {}).get("name", "?")
                msg = new_commits[0].get("commit", {}).get("message", "").split("\n")[0]
                branch = new_commits[0].get("commit", {}).get("tree", {}).get("sha", "")[:7]

                await self._bus.emit(
                    EventType.PUSH,
                    project=project_name,
                    message=f"{count} commit(s) pushed by {author}: {msg}",
                    source="github",
                    url=f"https://github.com/{owner_repo}/commit/{latest_sha}",
                    sha=latest_sha[:7],
                    author=author,
                    commit_message=msg,
                    commit_count=count,
                )

        # Check open PRs
        resp = await self._client.get(
            f"{GITHUB_API}/repos/{owner_repo}/pulls",
            params={"state": "all", "per_page": 5, "sort": "updated", "direction": "desc"},
        )
        if resp.status_code == 200:
            prs = resp.json()
            for pr in prs:
                pr_key = f"{owner_repo}:pr:{pr['number']}"
                prev_state = self._last_seen.get(pr_key)
                current_state = f"{pr['state']}:{pr.get('merged_at', '')}"

                if prev_state != current_state and notify and prev_state is not None:
                    if pr.get("merged_at") and "merged" not in (prev_state or ""):
                        await self._bus.emit(
                            EventType.PR_MERGED,
                            project=project_name,
                            message=f"PR #{pr['number']} merged: {pr['title']}",
                            source="github",
                            url=pr["html_url"],
                            pr_number=pr["number"],
                            pr_title=pr["title"],
                            author=pr["user"]["login"],
                        )
                    elif pr["state"] == "open" and prev_state is None:
                        await self._bus.emit(
                            EventType.PR_OPENED,
                            project=project_name,
                            message=f"PR #{pr['number']} opened: {pr['title']}",
                            source="github",
                            url=pr["html_url"],
                            pr_number=pr["number"],
                            pr_title=pr["title"],
                            author=pr["user"]["login"],
                        )

                self._last_seen[pr_key] = current_state

    # ── Webhook handler ───────────────────────────────────────────────────

    async def handle_webhook(self, request: Request) -> Response:
        """Handle incoming GitHub webhook POST."""
        body = await request.body()

        # Verify signature if secret is configured
        if self._webhook_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(
                self._webhook_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return Response(status_code=403, content="Invalid signature")

        event_type = request.headers.get("X-GitHub-Event", "")
        payload = await request.json()

        # Find project name from repo
        repo_url = payload.get("repository", {}).get("html_url", "")
        project_name = "unknown"
        for name, url in self._repos.items():
            if _extract_owner_repo(url) and repo_url.endswith(_extract_owner_repo(url)):
                project_name = name
                break

        if event_type == "push":
            ref = payload.get("ref", "")
            commits = payload.get("commits", [])
            pusher = payload.get("pusher", {}).get("name", "?")
            head_msg = commits[0]["message"].split("\n")[0] if commits else ""

            await self._bus.emit(
                EventType.PUSH,
                project=project_name,
                message=f"{len(commits)} commit(s) pushed by {pusher} to {ref}: {head_msg}",
                source="github",
                url=payload.get("compare", ""),
                branch=ref.replace("refs/heads/", ""),
                author=pusher,
                commit_count=len(commits),
            )

        elif event_type == "pull_request":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            pr_num = pr.get("number", 0)
            title = pr.get("title", "")
            author = pr.get("user", {}).get("login", "?")

            if action == "opened":
                await self._bus.emit(
                    EventType.PR_OPENED, project=project_name,
                    message=f"PR #{pr_num} opened by {author}: {title}",
                    source="github", url=pr.get("html_url", ""),
                    pr_number=pr_num, pr_title=title, author=author,
                )
            elif action == "closed" and pr.get("merged"):
                await self._bus.emit(
                    EventType.PR_MERGED, project=project_name,
                    message=f"PR #{pr_num} merged: {title}",
                    source="github", url=pr.get("html_url", ""),
                    pr_number=pr_num, pr_title=title, author=author,
                )

        elif event_type == "deployment_status":
            state = payload.get("deployment_status", {}).get("state", "")
            env = payload.get("deployment_status", {}).get("environment", "")
            url = payload.get("deployment_status", {}).get("target_url", "")

            etype = {
                "success": EventType.DEPLOY_SUCCESS,
                "failure": EventType.DEPLOY_FAILED,
                "pending": EventType.DEPLOY_STARTED,
            }.get(state, EventType.DEPLOY_STARTED)

            await self._bus.emit(
                etype, project=project_name,
                message=f"Deploy {state} on {env}",
                source="github", url=url, environment=env,
            )

        return Response(status_code=200, content="OK")

    # ── Status ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "repos_tracked": len(self._repos),
            "repos": {name: repo for name, repo in self._repos.items()},
            "running": self._running,
            "poll_interval": self._poll_interval,
        }
