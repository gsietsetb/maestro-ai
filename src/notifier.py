"""Unified proactive notification system – Telegram + WhatsApp.

Sends notifications to all configured channels when events happen,
WITHOUT requiring a user message first (proactive push).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from src.events import Event

logger = logging.getLogger(__name__)


class ProactiveNotifier:
    """Send proactive notifications to Telegram and WhatsApp.

    Doesn't need a Telegram Update context – sends directly via Bot API.
    Uses the WA Bridge HTTP API for WhatsApp messages.
    """

    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        wa_bridge_url: str = "http://localhost:3001",
        wa_number: str = "",
        enabled: bool = True,
    ):
        self._tg_token = telegram_token
        self._tg_chat_id = telegram_chat_id
        self._wa_bridge_url = wa_bridge_url
        self._wa_number = wa_number
        self._enabled = enabled
        self._client = httpx.AsyncClient(timeout=15.0)

    @property
    def telegram_configured(self) -> bool:
        return bool(self._tg_token and self._tg_chat_id)

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self._wa_bridge_url and self._wa_number)

    def set_telegram_chat_id(self, chat_id: str | int) -> None:
        """Store the user's Telegram chat_id (auto-detected from first message)."""
        self._tg_chat_id = str(chat_id)
        logger.info("Telegram chat_id set: %s", chat_id)

    async def close(self) -> None:
        await self._client.aclose()

    # ── Telegram ──────────────────────────────────────────────────────────

    async def send_telegram(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a message directly via Telegram Bot API."""
        if not self.telegram_configured:
            return False

        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        try:
            resp = await self._client.post(url, json={
                "chat_id": self._tg_chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            })
            if resp.status_code != 200:
                # Retry without parse_mode (Markdown escaping issues)
                resp = await self._client.post(url, json={
                    "chat_id": self._tg_chat_id,
                    "text": text.replace("*", "").replace("_", "").replace("`", ""),
                    "disable_web_page_preview": True,
                })
            return resp.status_code == 200
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    # ── WhatsApp (via WA Bridge) ──────────────────────────────────────────

    async def send_whatsapp(self, text: str) -> bool:
        """Send a message via the WA Bridge HTTP API."""
        if not self.whatsapp_configured:
            return False

        try:
            resp = await self._client.post(
                f"{self._wa_bridge_url}/send",
                json={"to": self._wa_number, "text": text},
            )
            data = resp.json()
            return data.get("success", False)
        except Exception as e:
            logger.error("WhatsApp send failed: %s", e)
            return False

    # ── Unified ───────────────────────────────────────────────────────────

    async def notify_all(self, text: str) -> dict[str, bool]:
        """Send to all configured channels."""
        if not self._enabled:
            return {"telegram": False, "whatsapp": False}

        results = {}
        if self.telegram_configured:
            results["telegram"] = await self.send_telegram(text)
        if self.whatsapp_configured:
            results["whatsapp"] = await self.send_whatsapp(
                text.replace("*", "").replace("_", "").replace("`", "")
            )
        return results

    async def notify_event(self, event: Event) -> None:
        """Format and send an event notification to all channels."""
        if not event.should_notify or not self._enabled:
            return

        text = event.format_notification()
        results = await self.notify_all(text)
        sent = [ch for ch, ok in results.items() if ok]
        if sent:
            logger.info("Notified %s: %s/%s", ", ".join(sent), event.type.value, event.project)
