"""Central configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings – values come from .env or real env vars."""

    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(default="", description="Telegram bot token from @BotFather")
    telegram_allowed_user_ids: str = Field(
        "", description="Comma-separated Telegram user IDs allowed to use the bot"
    )

    # ── WhatsApp Business Cloud API ───────────────────────────────────────
    whatsapp_token: str = Field(default="", description="WhatsApp Cloud API permanent token")
    whatsapp_phone_number_id: str = Field(default="", description="WhatsApp phone number ID")
    whatsapp_verify_token: str = Field(default="", description="Webhook verification token")
    whatsapp_allowed_numbers: str = Field(
        "", description="Comma-separated phone numbers allowed (e.g. 34692842705)"
    )

    # ── Gemini API (primary AI – intent parsing + voice transcription) ────
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini model for intent parsing")

    # ── Claude API (optional – kept for Cursor executor prompts) ──────────
    anthropic_api_key: str = Field(default="", description="Anthropic API key (optional)")

    # ── Cursor Background Agent API ───────────────────────────────────────
    cursor_api_key: str = Field(default="", description="Cursor API key for background agents")

    # ── WebSocket ─────────────────────────────────────────────────────────
    ws_secret: str = Field(default="change-me", description="Shared secret for WS auth")
    ws_port: int = Field(default=8765, description="WebSocket port for local agent")

    # ── Server ────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", description="Server bind host")
    port: int = Field(default=8000, description="Server bind port")
    webhook_url: str = Field(default="", description="Public URL for webhooks")
    log_level: str = Field(default="INFO", description="Logging level")

    # ── Database ──────────────────────────────────────────────────────────
    database_path: str = Field(default="data/orchestrator.db", description="SQLite DB path")

    # ── Home Assistant (domotica) ─────────────────────────────────────────
    ha_url: str = Field(default="", description="Home Assistant URL (e.g. http://192.168.1.100:8123)")
    ha_token: str = Field(default="", description="HA long-lived access token")

    # ── WhatsApp Web Bridge (Baileys) ─────────────────────────────────────
    wa_bridge_url: str = Field(default="http://localhost:3001", description="WA bridge HTTP URL")
    wa_bridge_enabled: bool = Field(default=True, description="Enable WA Web bridge")

    # ── Music AI Microservice ──────────────────────────────────────────────
    music_ai_url: str = Field(default="http://localhost:8001", description="Music AI service URL")

    # ── Watch Companion ────────────────────────────────────────────────────
    watch_enabled: bool = Field(default=True, description="Enable Watch command queue")

    # ── Projects ──────────────────────────────────────────────────────────
    projects_file: str = Field(default="projects.yaml", description="Path to projects.yaml")

    # ── GitHub Integration ────────────────────────────────────────────────
    github_token: str = Field(default="", description="GitHub Personal Access Token (for polling API)")
    github_webhook_secret: str = Field(default="", description="GitHub webhook secret for signature verification")
    github_poll_interval: int = Field(default=60, description="Seconds between GitHub API polls")

    # ── Vercel Integration ────────────────────────────────────────────────
    vercel_token: str = Field(default="", description="Vercel API token")
    vercel_team_id: str = Field(default="", description="Vercel team ID (optional)")
    vercel_poll_interval: int = Field(default=60, description="Seconds between Vercel API polls")

    # ── Proactive Notifications ───────────────────────────────────────────
    notification_telegram_chat_id: str = Field(
        default="", description="Telegram chat_id for proactive notifications (auto-detected)"
    )
    notification_whatsapp_number: str = Field(
        default="34692842705", description="WhatsApp number for proactive notifications"
    )
    notification_enabled: bool = Field(default=True, description="Enable proactive notifications")

    # ── Ollama (local LLM) ──────────────────────────────────────────────
    ollama_url: str = Field(default="http://localhost:11434", description="Ollama API URL")
    ollama_model: str = Field(default="qwen3:4b", description="Default Ollama model")

    # ── Project Monitor ───────────────────────────────────────────────────
    project_monitor_interval: int = Field(default=300, description="Seconds between local project scans")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── Helpers ───────────────────────────────────────────────────────────

    @property
    def allowed_user_ids(self) -> set[int]:
        """Return the set of allowed Telegram user IDs."""
        if not self.telegram_allowed_user_ids:
            return set()
        return {int(uid.strip()) for uid in self.telegram_allowed_user_ids.split(",") if uid.strip()}

    @property
    def allowed_whatsapp_numbers(self) -> set[str]:
        """Return the set of allowed WhatsApp phone numbers."""
        if not self.whatsapp_allowed_numbers:
            return set()
        return {n.strip() for n in self.whatsapp_allowed_numbers.split(",") if n.strip()}

    @property
    def db_path(self) -> Path:
        p = Path(self.database_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_token and self.whatsapp_phone_number_id)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def ha_enabled(self) -> bool:
        return bool(self.ha_url and self.ha_token)

    @property
    def music_ai_enabled(self) -> bool:
        return bool(self.music_ai_url)

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_token)

    @property
    def vercel_enabled(self) -> bool:
        return bool(self.vercel_token)


def get_settings() -> Settings:
    """Singleton-ish loader so we parse env once."""
    return Settings()  # type: ignore[call-arg]
