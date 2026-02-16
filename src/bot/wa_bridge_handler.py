"""WhatsApp Web Bridge handler.

Receives messages forwarded from the wa-bridge Node.js service (Baileys)
and sends responses back via the bridge's HTTP API.

No Meta Business API needed. Works with any personal WhatsApp number.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from typing import Optional

import httpx

from src.bot.voice_parser import VoiceParser
from src.orchestrator.intent_parser import IntentParser, ParsedIntent
from src.orchestrator.project_registry import ProjectRegistry
from src.orchestrator.router import ActionRouter, ExecutionResult
from src.orchestrator.task_tracker import TaskTracker
from src.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)

# Destructive actions requiring confirmation
DESTRUCTIVE_ACTIONS = {"deploy", "git_force_push", "delete_branch"}


class WABridgeHandler:
    """Handles messages from the WhatsApp Web bridge."""

    def __init__(
        self,
        bridge_url: str,
        allowed_numbers: set[str],
        parser: IntentParser,
        registry: ProjectRegistry,
        router: ActionRouter,
        tracker: TaskTracker,
        voice_parser: VoiceParser | None = None,
        gemini: GeminiProvider | None = None,
    ):
        self._bridge_url = bridge_url.rstrip("/")
        self._allowed = allowed_numbers
        self._parser = parser
        self._registry = registry
        self._router = router
        self._tracker = tracker
        self._voice_parser = voice_parser
        self._gemini = gemini
        self._client = httpx.AsyncClient(timeout=30.0)
        # Pending confirmations and last results per sender
        self._pending: dict[str, ParsedIntent] = {}
        self._last_results: dict[str, dict] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # ── Send messages via bridge ─────────────────────────────────────────────

    async def _send_text(self, to: str, text: str) -> None:
        """Send a text message via the WA bridge."""
        try:
            await self._client.post(
                f"{self._bridge_url}/send",
                json={"to": to, "text": text},
            )
        except Exception as e:
            logger.error("Failed to send via WA bridge: %s", e)

    # ── Process incoming message from bridge ─────────────────────────────────

    async def handle_incoming(self, data: dict) -> dict:
        """Process an incoming message from the WA bridge.

        Called by the FastAPI endpoint /wa-bridge/incoming.
        """
        sender = data.get("sender", "")
        text = data.get("text", "")
        media_type = data.get("media_type")
        media_data = data.get("media_base64")

        # Auth check
        if self._allowed and sender not in self._allowed:
            logger.warning("Unauthorized WA bridge message from %s", sender)
            return {"status": "unauthorized"}

        # Process in background to not block the bridge
        asyncio.create_task(
            self._process(sender, text, media_type, media_data)
        )
        return {"status": "ok"}

    async def _process(
        self,
        sender: str,
        text: str,
        media_type: str | None,
        media_data: str | None,
    ) -> None:
        """Process a message asynchronously."""
        try:
            if media_type == "audio":
                await self._handle_audio(sender, media_data)
            elif media_type == "image":
                await self._handle_image(sender, text, media_data)
            elif media_type == "video":
                await self._handle_video(sender, text, media_data)
            elif text:
                await self._handle_text(sender, text)
        except Exception as e:
            logger.exception("Error processing WA bridge message from %s", sender)
            await self._send_text(sender, f"Error interno: {e}")

    # ── Text handler ─────────────────────────────────────────────────────────

    async def _handle_text(self, sender: str, text: str) -> None:
        """Handle a text message."""
        lower = text.strip().lower()

        # Check for detail request
        if lower in ("detalle", "detail", "mas", "more"):
            last = self._last_results.get(sender)
            if last:
                output = last.get("output", "Sin detalle.")
                if len(output) > 4000:
                    output = output[:4000] + "\n... (truncado)"
                await self._send_text(sender, output)
            else:
                await self._send_text(sender, "No hay resultado previo.")
            return

        # Check for confirmation responses
        if lower in ("si", "sí", "yes", "ok", "confirmar"):
            pending = self._pending.pop(sender, None)
            if pending:
                await self._send_text(sender, "Ejecutando...")
                await self._execute_intent(sender, pending)
                return

        if lower in ("no", "cancelar", "cancel"):
            if self._pending.pop(sender, None):
                await self._send_text(sender, "Cancelado.")
                return

        # Parse intent
        try:
            user_id = hash(sender) & 0x7FFFFFFF
            intent = await self._parser.parse(
                text, self._registry.project_names(), user_id=user_id
            )
        except Exception as e:
            logger.exception("Intent parsing failed")
            await self._send_text(
                sender,
                f"No entendi el mensaje. Intenta de nuevo.\nDetalle: {e}",
            )
            return

        # Check destructive actions
        if intent.action in DESTRUCTIVE_ACTIONS:
            self._pending[sender] = intent
            await self._send_text(
                sender,
                f"Confirmar {intent.action} en {intent.project}?\n"
                f'Responde "si" o "no".',
            )
            return

        await self._execute_intent(sender, intent)

    # ── Audio handler ────────────────────────────────────────────────────────

    async def _handle_audio(self, sender: str, media_json: str | None) -> None:
        """Handle voice/audio messages – transcribe and process."""
        if not self._voice_parser or not media_json:
            await self._send_text(sender, "Transcripcion de voz no disponible.")
            return

        import json
        media = json.loads(media_json)
        audio_bytes = base64.b64decode(media["data"])
        mime = media.get("mime", "audio/ogg")

        await self._send_text(sender, "Transcribiendo audio...")
        raw, cleaned = await self._voice_parser.transcribe_and_parse(
            audio_data=audio_bytes, mime_type=mime
        )

        if not cleaned:
            await self._send_text(sender, "No pude transcribir el audio.")
            return

        await self._send_text(sender, f'Transcripcion: "{cleaned}"\n\nProcesando...')
        await self._handle_text(sender, cleaned)

    # ── Image handler ────────────────────────────────────────────────────────

    async def _handle_image(
        self, sender: str, caption: str, media_json: str | None
    ) -> None:
        """Handle image messages."""
        if not media_json:
            return

        import json
        media = json.loads(media_json)
        image_bytes = base64.b64decode(media["data"])
        mime = media.get("mime", "image/jpeg")

        if caption:
            # Image + text → parse intent with image
            try:
                user_id = hash(sender) & 0x7FFFFFFF
                intent = await self._parser.parse(
                    caption, self._registry.project_names(), user_id=user_id
                )
                intent.image_data = image_bytes
            except Exception as e:
                await self._send_text(sender, f"Error: {e}")
                return
            await self._execute_intent(sender, intent)
        else:
            # Image without text → Gemini Vision analysis
            if not self._gemini:
                await self._send_text(sender, "Envia la imagen con texto o configura Gemini.")
                return
            await self._send_text(sender, "Analizando imagen...")
            try:
                analysis = await self._gemini.generate_multimodal(
                    prompt=(
                        "Analyze this image in detail. If it's code, explain it. "
                        "If it's UI, describe it. Respond in Spanish."
                    ),
                    media_data=image_bytes,
                    mime_type=mime,
                    temperature=0.4,
                    max_tokens=4096,
                )
                await self._send_text(sender, analysis[:4000] if analysis else "No pude analizar la imagen.")
            except Exception as e:
                await self._send_text(sender, f"Error analizando imagen: {e}")

    # ── Video handler ────────────────────────────────────────────────────────

    async def _handle_video(
        self, sender: str, caption: str, media_json: str | None
    ) -> None:
        """Handle video messages."""
        if not self._gemini or not media_json:
            await self._send_text(sender, "Analisis de video no disponible.")
            return

        import json
        media = json.loads(media_json)
        video_bytes = base64.b64decode(media["data"])
        mime = media.get("mime", "video/mp4")

        await self._send_text(sender, "Analizando video...")
        try:
            prompt = caption or (
                "Analyze this video. Describe what you see, any code, UI, bugs. "
                "Respond in Spanish."
            )
            analysis = await self._gemini.generate_multimodal(
                prompt=prompt,
                media_data=video_bytes,
                mime_type=mime,
                temperature=0.4,
                max_tokens=4096,
            )
            await self._send_text(
                sender, analysis[:4000] if analysis else "No pude analizar el video."
            )
        except Exception as e:
            await self._send_text(sender, f"Error analizando video: {e}")

    # ── Intent execution ─────────────────────────────────────────────────────

    async def _execute_intent(self, sender: str, intent: ParsedIntent) -> None:
        """Execute an intent and send the result."""
        task_id = str(uuid.uuid4())
        await self._tracker.create(task_id, intent, status="running")

        project_name = intent.project or "global"
        await self._send_text(
            sender,
            f"Tarea lanzada\n"
            f"Proyecto: {project_name}\n"
            f"Accion: {intent.action}\n"
            f"ID: {task_id[:8]}",
        )

        # Notify callback for improvement loop
        async def _notify(msg: str) -> None:
            await self._send_text(sender, f"🔄 {msg}")

        try:
            result = await self._router.route(intent, task_id, notify=_notify)
            await self._tracker.complete(
                task_id, success=result.success, output=result.output
            )

            # Store for detail request
            self._last_results[sender] = {
                "output": result.output,
                "success": result.success,
            }

            # Summarize
            status = "OK" if result.success else "FALLO"
            summary = result.output

            if self._gemini and len(result.output) > 100:
                try:
                    summary = await self._gemini.generate(
                        prompt=(
                            f"Resume en 2 lineas breves este resultado ({intent.action}). "
                            f"Sin markdown ni emojis. En espanol:\n\n"
                            f"{result.output[:3000]}"
                        ),
                        temperature=0.3,
                        max_tokens=400,
                        disable_thinking=True,
                    )
                    if not summary or len(summary.strip()) < 5:
                        lines = [
                            l.strip()
                            for l in result.output.splitlines()
                            if l.strip()
                        ]
                        summary = "\n".join(lines[:2])
                    else:
                        summary = summary.strip()
                except Exception:
                    lines = [
                        l.strip() for l in result.output.splitlines() if l.strip()
                    ]
                    summary = "\n".join(lines[:2])

            msg = f"[{status}] {project_name} / {intent.action}\n\n{summary}"
            if result.pr_url:
                msg = f"[{status}] {project_name} / {intent.action}\nPR: {result.pr_url}\n\n{summary}"

            await self._send_text(sender, msg)

            if len(result.output) > 200:
                await self._send_text(sender, 'Responde "detalle" para ver el output completo.')

        except Exception as e:
            logger.exception("Task execution failed: %s", task_id)
            await self._tracker.complete(task_id, success=False, output=str(e))
            await self._send_text(sender, f"Error ejecutando tarea: {e}")
