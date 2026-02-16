"""WhatsApp Business Cloud API handlers.

Processes incoming messages via Meta's Webhook API and sends responses
back through the WhatsApp Cloud API.

Setup:
1. Create a Meta Business app: https://developers.facebook.com/
2. Add WhatsApp product to your app
3. Get: WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_VERIFY_TOKEN
4. Set webhook URL to: https://your-server.com/whatsapp/webhook

Supports: text messages, voice messages, images, videos, documents (audio/image/video).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

import httpx
from fastapi import Request, Response

from src.bot.formatters import escape_md
from src.bot.voice_parser import VoiceParser
from src.orchestrator.intent_parser import IntentParser, ParsedIntent
from src.orchestrator.project_registry import ProjectRegistry
from src.orchestrator.router import ActionRouter, ExecutionResult
from src.orchestrator.task_tracker import TaskTracker
from src.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = "https://graph.facebook.com/v21.0"

# Destructive actions that require confirmation
DESTRUCTIVE_ACTIONS = {"deploy", "git_force_push", "delete_branch"}


class WhatsAppHandler:
    """Handles WhatsApp Business Cloud API webhooks and message sending."""

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        verify_token: str,
        allowed_numbers: set[str],
        parser: IntentParser,
        registry: ProjectRegistry,
        router: ActionRouter,
        tracker: TaskTracker,
        voice_parser: Optional[VoiceParser] = None,
        gemini: Optional[GeminiProvider] = None,
    ):
        self._token = token
        self._phone_id = phone_number_id
        self._verify_token = verify_token
        self._allowed_numbers = allowed_numbers
        self._parser = parser
        self._registry = registry
        self._router = router
        self._tracker = tracker
        self._voice_parser = voice_parser
        self._gemini = gemini
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        # Pending confirmations: {phone_number: ParsedIntent}
        self._pending: dict[str, ParsedIntent] = {}

    async def close(self) -> None:
        await self._client.aclose()

    # ── Webhook verification (GET) ────────────────────────────────────────

    async def verify_webhook(self, request: Request) -> Response:
        """Handle the webhook verification challenge from Meta."""
        mode = request.query_params.get("hub.mode")
        token = request.query_params.get("hub.verify_token")
        challenge = request.query_params.get("hub.challenge")

        if mode == "subscribe" and token == self._verify_token:
            logger.info("WhatsApp webhook verified")
            return Response(content=challenge, media_type="text/plain")

        logger.warning("WhatsApp webhook verification failed")
        return Response(status_code=403, content="Forbidden")

    # ── Webhook handler (POST) ────────────────────────────────────────────

    async def handle_webhook(self, request: Request) -> dict:
        """Process incoming WhatsApp webhook events."""
        body = await request.json()

        # Extract messages from the webhook payload
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                for message in messages:
                    await self._process_message(message)

        return {"status": "ok"}

    # ── Message processing ────────────────────────────────────────────────

    async def _process_message(self, message: dict) -> None:
        """Route an incoming message by type."""
        sender = message.get("from", "")
        msg_type = message.get("type", "")

        # Auth check
        if self._allowed_numbers and sender not in self._allowed_numbers:
            logger.warning("Unauthorized WhatsApp message from %s", sender)
            return

        logger.info("WhatsApp message: type=%s, from=%s", msg_type, sender)

        if msg_type == "text":
            text = message.get("text", {}).get("body", "")
            await self._handle_text(sender, text)

        elif msg_type == "audio":
            audio_info = message.get("audio", {})
            await self._handle_audio(sender, audio_info)

        elif msg_type == "image":
            image_info = message.get("image", {})
            caption = image_info.get("caption", "")
            await self._handle_image(sender, image_info, caption)

        elif msg_type == "video":
            video_info = message.get("video", {})
            caption = video_info.get("caption", "")
            await self._handle_video(sender, video_info, caption)

        elif msg_type == "document":
            doc_info = message.get("document", {})
            caption = doc_info.get("caption", "")
            await self._handle_document(sender, doc_info, caption)

        elif msg_type == "interactive":
            interactive = message.get("interactive", {})
            button_reply = interactive.get("button_reply", {})
            if button_reply:
                await self._handle_button_reply(sender, button_reply.get("id", ""))

        else:
            await self._send_text(
                sender, "Puedo procesar: texto, audio, imagenes, videos y documentos."
            )

    async def _handle_text(self, sender: str, text: str) -> None:
        """Handle a text message."""
        if not text:
            return

        # Check for "detalle" request (full output)
        if text.lower() in ("detalle", "detail", "ver detalle", "output"):
            detail_key = f"detail:{sender}"
            full_output = self._pending.pop(detail_key, None)
            if full_output:
                out = full_output[:3000] if len(full_output) > 3000 else full_output
                await self._send_text(sender, f"Output completo:\n\n{out}")
                return
            await self._send_text(sender, "No hay detalle pendiente.")
            return

        # Check for pending confirmation replies
        if text.lower() in ("si", "sí", "yes", "confirmar", "ok"):
            pending = self._pending.pop(sender, None)
            if pending and isinstance(pending, ParsedIntent):
                await self._send_text(sender, "Ejecutando...")
                await self._execute_intent(sender, pending)
                return

        if text.lower() in ("no", "cancelar", "cancel"):
            pending = self._pending.pop(sender, None)
            if pending and isinstance(pending, ParsedIntent):
                await self._send_text(sender, "Cancelado.")
                return

        # Parse intent
        try:
            # Use phone number hash as user_id for conversation history
            user_id = hash(sender) & 0x7FFFFFFF
            intent = await self._parser.parse(text, self._registry.project_names(), user_id=user_id)
        except Exception as e:
            logger.exception("Intent parsing failed")
            await self._send_text(sender, f"Error: {e}")
            return

        # Validate project
        if intent.project:
            project_info = self._registry.resolve(intent.project)
            if not project_info:
                await self._send_text(sender, f"Proyecto '{intent.project}' no encontrado.")
                return

        # Destructive actions need confirmation
        if intent.action in DESTRUCTIVE_ACTIONS:
            self._pending[sender] = intent
            await self._send_text(
                sender,
                f"Confirmar *{intent.action}* en *{intent.project}*?\n"
                f"Responde 'si' o 'no'.",
            )
            return

        await self._execute_intent(sender, intent)

    async def _handle_audio(self, sender: str, audio_info: dict) -> None:
        """Handle a voice/audio message – transcribe and process."""
        if not self._voice_parser:
            await self._send_text(sender, "Transcripcion de voz no configurada.")
            return

        media_id = audio_info.get("id")
        if not media_id:
            return

        # Download the audio file from WhatsApp
        audio_data = await self._download_media(media_id)
        if not audio_data:
            await self._send_text(sender, "No pude descargar el audio.")
            return

        mime_type = audio_info.get("mime_type", "audio/ogg")

        await self._send_text(sender, "Transcribiendo audio...")

        raw, cleaned = await self._voice_parser.transcribe_and_parse(
            audio_data=audio_data,
            mime_type=mime_type,
        )

        if not cleaned:
            await self._send_text(sender, "No pude transcribir el audio. Intenta de nuevo.")
            return

        await self._send_text(sender, f'Transcripcion: "{cleaned}"\n\nProcesando...')

        # Parse the transcribed text
        await self._handle_text(sender, cleaned)

    async def _handle_image(self, sender: str, image_info: dict, caption: str) -> None:
        """Handle an image message – with caption: parse intent, without: Gemini Vision."""
        media_id = image_info.get("id")
        if not media_id:
            return

        image_data = await self._download_media(media_id)
        if not image_data:
            await self._send_text(sender, "No pude descargar la imagen.")
            return

        if caption:
            # Image + text → parse intent and attach image
            try:
                user_id = hash(sender) & 0x7FFFFFFF
                intent = await self._parser.parse(
                    caption, self._registry.project_names(), user_id=user_id
                )
                intent.image_data = image_data
            except Exception as e:
                await self._send_text(sender, f"Error: {e}")
                return
            await self._execute_intent(sender, intent)
        else:
            # Image sin texto → Gemini Vision analiza
            if not self._gemini:
                await self._send_text(sender, "Envia la imagen con un texto o configura Gemini.")
                return
            await self._send_text(sender, "Analizando imagen...")
            try:
                mime = image_info.get("mime_type", "image/jpeg")
                analysis = await self._gemini.generate_multimodal(
                    prompt=(
                        "Analyze this image in detail. If it's a screenshot of code, explain the code, "
                        "any errors or bugs, and suggest fixes. If it's a UI, describe it. "
                        "Respond in Spanish."
                    ),
                    media_data=image_data,
                    mime_type=mime,
                    temperature=0.4,
                    max_tokens=4096,
                )
                if analysis:
                    await self._send_text(sender, analysis[:4000])
                else:
                    await self._send_text(sender, "No pude analizar la imagen.")
            except Exception as e:
                await self._send_text(sender, f"Error analizando imagen: {e}")

    async def _handle_video(self, sender: str, video_info: dict, caption: str) -> None:
        """Handle a video message – analyze with Gemini multimodal."""
        if not self._gemini:
            await self._send_text(sender, "Analisis de video no disponible (configura Gemini).")
            return

        media_id = video_info.get("id")
        if not media_id:
            return

        # Size check (~15MB limit for Gemini inline)
        file_size = video_info.get("file_size", 0)
        if file_size and file_size > 15 * 1024 * 1024:
            await self._send_text(
                sender,
                f"Video demasiado grande ({file_size // (1024 * 1024)}MB). Max ~15MB.",
            )
            return

        await self._send_text(sender, "Descargando y analizando video...")
        video_data = await self._download_media(media_id)
        if not video_data:
            await self._send_text(sender, "No pude descargar el video.")
            return

        try:
            mime = video_info.get("mime_type", "video/mp4")
            prompt = caption or (
                "Analyze this video in detail. Describe what you see, any code, UI, "
                "bugs, or relevant information. Respond in Spanish."
            )
            analysis = await self._gemini.generate_multimodal(
                prompt=prompt,
                media_data=video_data,
                mime_type=mime,
                temperature=0.4,
                max_tokens=4096,
            )
            if analysis:
                await self._send_text(sender, analysis[:4000])
            else:
                await self._send_text(sender, "No pude analizar el video.")
        except Exception as e:
            await self._send_text(sender, f"Error analizando video: {e}")

    async def _handle_document(self, sender: str, doc_info: dict, caption: str) -> None:
        """Handle document messages (audio, image, video files sent as documents)."""
        media_id = doc_info.get("id")
        mime = doc_info.get("mime_type", "")
        file_name = doc_info.get("filename", "")

        if not media_id:
            return

        if mime.startswith("audio/"):
            # Audio document → transcribe
            if not self._voice_parser:
                await self._send_text(sender, "Transcripcion no disponible.")
                return
            await self._send_text(sender, f"Transcribiendo {file_name}...")
            data = await self._download_media(media_id)
            if not data:
                await self._send_text(sender, "No pude descargar el audio.")
                return
            raw, cleaned = await self._voice_parser.transcribe_and_parse(data, mime)
            if cleaned:
                await self._send_text(sender, f'Transcripcion:\n\n"{cleaned}"')
            else:
                await self._send_text(sender, "No pude transcribir el audio.")

        elif mime.startswith("image/") and self._gemini:
            data = await self._download_media(media_id)
            if not data:
                await self._send_text(sender, "No pude descargar la imagen.")
                return
            await self._send_text(sender, "Analizando imagen...")
            prompt = caption or "Describe and analyze this image. Respond in Spanish."
            analysis = await self._gemini.generate_multimodal(prompt, data, mime, temperature=0.4)
            await self._send_text(sender, analysis[:4000] if analysis else "No pude analizar.")

        elif mime.startswith("video/") and self._gemini:
            data = await self._download_media(media_id)
            if not data:
                await self._send_text(sender, "No pude descargar el video.")
                return
            await self._send_text(sender, "Analizando video...")
            prompt = caption or "Analyze this video. Respond in Spanish."
            analysis = await self._gemini.generate_multimodal(prompt, data, mime, temperature=0.4)
            await self._send_text(sender, analysis[:4000] if analysis else "No pude analizar.")

        else:
            await self._send_text(sender, f"Archivo {file_name} ({mime}): solo proceso audio, imagenes y videos.")

    async def _handle_button_reply(self, sender: str, button_id: str) -> None:
        """Handle interactive button replies."""
        if button_id == "confirm_yes":
            pending = self._pending.pop(sender, None)
            if pending:
                await self._send_text(sender, "Ejecutando...")
                await self._execute_intent(sender, pending)
        elif button_id == "confirm_no":
            self._pending.pop(sender, None)
            await self._send_text(sender, "Cancelado.")

    # ── Intent execution ──────────────────────────────────────────────────

    async def _execute_intent(self, sender: str, intent: ParsedIntent) -> None:
        """Execute an intent and send the result."""
        task_id = str(uuid.uuid4())
        await self._tracker.create(task_id, intent, status="running")

        project_name = intent.project or "global"
        await self._send_text(
            sender,
            f"Tarea lanzada\n\n"
            f"Proyecto: {project_name}\n"
            f"Accion: {intent.action}\n"
            f"ID: {task_id[:8]}\n\n"
            f"Te aviso cuando termine.",
        )

        # Build notify callback for improvement loop progress
        async def _notify(msg: str) -> None:
            try:
                await self._send_text(sender, f"🔄 {msg}")
            except Exception:
                pass

        try:
            result: ExecutionResult = await self._router.route(intent, task_id, notify=_notify)
            await self._tracker.complete(task_id, success=result.success, output=result.output)

            status = "OK" if result.success else "FALLO"

            # Generate 2-line summary via Gemini
            summary = result.output
            if self._gemini and len(result.output) > 100:
                try:
                    summary = await self._gemini.generate(
                        prompt=(
                            f"Resume en 2 lineas breves este resultado de una tarea ({intent.action}). "
                            f"Di que se hizo, si hay PR o archivos, y si tests pasaron. "
                            f"Sin markdown ni emojis. En espanol, directo:\n\n"
                            f"{result.output[:3000]}"
                        ),
                        temperature=0.3,
                        max_tokens=400,
                        disable_thinking=True,
                    )
                    if not summary or len(summary.strip()) < 5:
                        lines = [l.strip() for l in result.output.splitlines() if l.strip()]
                        summary = "\n".join(lines[:2])
                    else:
                        summary = summary.strip()
                except Exception:
                    lines = [l.strip() for l in result.output.splitlines() if l.strip()]
                    summary = "\n".join(lines[:2])

            msg = f"[{status}] {project_name} / {intent.action}\n\n{summary}"
            if result.pr_url:
                msg = f"[{status}] {project_name} / {intent.action}\nPR: {result.pr_url}\n\n{summary}"

            await self._send_text(sender, msg)

            # Also offer detail via "detalle" keyword in WhatsApp
            if len(result.output) > 200:
                await self._send_text(
                    sender,
                    'Responde "detalle" para ver el output completo.',
                )
                # Store for potential detail request
                self._pending[f"detail:{sender}"] = result.output

        except Exception as e:
            logger.exception("Task execution failed")
            await self._tracker.complete(task_id, success=False, output=str(e))
            await self._send_text(sender, f"Error ejecutando tarea: {e}")

    # ── WhatsApp Cloud API: sending messages ──────────────────────────────

    async def _send_text(self, to: str, text: str) -> None:
        """Send a text message via WhatsApp Cloud API."""
        url = f"{WHATSAPP_API_URL}/{self._phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("WhatsApp send error: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Failed to send WhatsApp message: %s", e)

    async def _send_buttons(
        self, to: str, body: str, buttons: list[dict[str, str]]
    ) -> None:
        """Send an interactive button message."""
        url = f"{WHATSAPP_API_URL}/{self._phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": b["id"], "title": b["title"]},
                        }
                        for b in buttons[:3]  # WhatsApp limit: 3 buttons
                    ]
                },
            },
        }

        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("WhatsApp button send error: %s", resp.text[:200])
        except Exception as e:
            logger.error("Failed to send WhatsApp buttons: %s", e)

    # ── Media download ────────────────────────────────────────────────────

    async def _download_media(self, media_id: str) -> Optional[bytes]:
        """Download media from WhatsApp (2-step: get URL, then download)."""
        try:
            # Step 1: Get media URL
            url_resp = await self._client.get(f"{WHATSAPP_API_URL}/{media_id}")
            url_resp.raise_for_status()
            media_url = url_resp.json().get("url")
            if not media_url:
                return None

            # Step 2: Download the actual file
            dl_resp = await self._client.get(media_url)
            dl_resp.raise_for_status()
            return dl_resp.content

        except Exception as e:
            logger.error("Failed to download WhatsApp media %s: %s", media_id, e)
            return None
