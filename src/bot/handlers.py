"""Telegram bot handlers – full message processing and command routing.

Supports: text, voice, audio files, photos, videos, video notes, documents, inline keyboards.
Features: code, operations, git, conversations, domotica, mesh status, Gemini Vision analysis.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.bot.formatters import (
    escape_md,
    format_domotica_status,
    format_error,
    format_help,
    format_mesh_status,
    format_project_list,
    format_task_launched,
    format_task_result,
    format_task_summary,
)
from src.bot.keyboards import action_menu, confirm_keyboard, detail_button, domotica_menu
from src.bot.voice_parser import VoiceParser
from src.config import Settings
from src.events import EventBus, EventType
from src.notifier import ProactiveNotifier
from src.providers.gemini import GeminiProvider
from src.executors.agent_mesh import AgentMesh
from src.executors.homeassistant_executor import HomeAssistantExecutor
from src.orchestrator.intent_parser import IntentParser, ParsedIntent
from src.orchestrator.project_registry import ProjectRegistry
from src.orchestrator.router import ActionRouter
from src.orchestrator.task_tracker import TaskTracker

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Destructive actions that require confirmation
DESTRUCTIVE_ACTIONS = {"deploy", "git_force_push", "delete_branch"}


# ── Auth decorator ───────────────────────────────────────────────────────────


def authorized(func):
    """Only allow whitelisted Telegram user IDs."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        settings: Settings = context.bot_data["settings"]
        user_id = update.effective_user.id if update.effective_user else None

        if settings.allowed_user_ids and user_id not in settings.allowed_user_ids:
            logger.warning("Unauthorized access attempt from user_id=%s", user_id)
            if update.effective_message:
                await update.effective_message.reply_text("No autorizado.")
            return

        return await func(update, context)

    return wrapper


# ── Command handlers ─────────────────────────────────────────────────────────


@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start."""
    await update.effective_message.reply_text(
        format_help(),
        parse_mode="MarkdownV2",
        reply_markup=action_menu(),
    )


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help."""
    await update.effective_message.reply_text(format_help(), parse_mode="MarkdownV2")


@authorized
async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /projects – list all registered projects."""
    registry: ProjectRegistry = context.bot_data["registry"]
    await update.effective_message.reply_text(
        format_project_list(registry.all_projects()),
        parse_mode="MarkdownV2",
    )


@authorized
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tasks – show active tasks."""
    tracker: TaskTracker = context.bot_data["tracker"]
    active = await tracker.list_active()
    if not active:
        await update.effective_message.reply_text("No hay tareas activas.")
        return

    lines = ["*Tareas activas*\n"]
    for t in active:
        lines.append(f"  `{t['id'][:8]}` — {t['project']} / {t['action']} ({t['status']})")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


@authorized
async def cmd_casa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /casa – domotica quick menu and status."""
    ha: HomeAssistantExecutor | None = context.bot_data.get("ha_executor")
    if not ha:
        await update.effective_message.reply_text(
            "Domotica no configurada\\. Anade `HA_URL` y `HA_TOKEN` en \\.env",
            parse_mode="MarkdownV2",
        )
        return

    result = await ha.get_all_states()
    if result.success:
        await update.effective_message.reply_text(
            format_domotica_status(result.output),
            parse_mode="MarkdownV2",
            reply_markup=domotica_menu(),
        )
    else:
        await update.effective_message.reply_text(
            f"Error conectando con Home Assistant: {result.output}",
            reply_markup=domotica_menu(),
        )


@authorized
async def cmd_mesh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /mesh – show connected agents."""
    mesh: AgentMesh = context.bot_data["agent_mesh"]
    status = mesh.status_summary()
    await update.effective_message.reply_text(
        format_mesh_status(status),
        parse_mode="MarkdownV2",
    )


# ── Message handler (natural language) ───────────────────────────────────────


@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text messages – parse intent and route."""
    text = update.effective_message.text
    if not text:
        return

    # Auto-detect and store chat_id for proactive notifications
    notifier: ProactiveNotifier | None = context.bot_data.get("notifier")
    if notifier and update.effective_chat:
        chat_id = str(update.effective_chat.id)
        if not notifier._tg_chat_id or notifier._tg_chat_id != chat_id:
            notifier.set_telegram_chat_id(chat_id)

    parser: IntentParser = context.bot_data["parser"]
    registry: ProjectRegistry = context.bot_data["registry"]
    router: ActionRouter = context.bot_data["router"]
    tracker: TaskTracker = context.bot_data["tracker"]

    # Parse intent (with conversation history for context)
    user_id = update.effective_user.id if update.effective_user else None
    try:
        intent: ParsedIntent = await parser.parse(text, registry.project_names(), user_id=user_id)
    except Exception as e:
        logger.exception("Intent parsing failed")
        await update.effective_message.reply_text(
            f"No entendi el mensaje. Intenta de nuevo o usa /help.\n\nDetalle: {e}"
        )
        return

    # Validate project
    if intent.project:
        project_info = registry.resolve(intent.project)
        if not project_info:
            await update.effective_message.reply_text(
                f"Proyecto '{intent.project}' no encontrado. Usa /projects para ver la lista."
            )
            return

    # Check if destructive – require confirmation
    if intent.action in DESTRUCTIVE_ACTIONS:
        task_id = str(uuid.uuid4())
        await tracker.create(task_id, intent, status="pending_confirmation")
        context.user_data[f"pending:{task_id}"] = intent
        await update.effective_message.reply_text(
            f"Confirmar *{intent.action}* en `{intent.project}`?",
            parse_mode="MarkdownV2",
            reply_markup=confirm_keyboard(task_id),
        )
        return

    # Execute directly
    await _execute_intent(update, context, intent, router, tracker)


async def _summarize_output(output: str, action: str, gemini: GeminiProvider) -> str:
    """Use Gemini to create a concise 2-line summary of a task result."""
    if len(output) < 100:
        return output  # Already short enough

    try:
        summary = await gemini.generate(
            prompt=(
                f"Resume en 2 lineas breves este resultado de una tarea ({action}). "
                f"Di que se hizo, si hay PR o archivos, y si tests pasaron. "
                f"Sin markdown ni emojis. En espanol, directo:\n\n"
                f"{output[:3000]}"
            ),
            temperature=0.3,
            max_tokens=400,
            disable_thinking=True,
        )
        if summary and len(summary.strip()) > 10:
            return summary.strip()
    except Exception as e:
        logger.warning("Failed to summarize with Gemini: %s", e)

    # Fallback: first 2 meaningful lines
    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
    return "\n".join(lines[:2]) if lines else output[:200]


async def _execute_intent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    intent: ParsedIntent,
    router: ActionRouter,
    tracker: TaskTracker,
) -> None:
    """Create a task, execute via router, summarize result in 2 lines."""
    task_id = str(uuid.uuid4())
    await tracker.create(task_id, intent, status="running")

    chat_id = update.effective_chat.id
    project = intent.project or "global"

    # Notify user
    await update.effective_message.reply_text(
        format_task_launched(project, intent.action, task_id),
        parse_mode="MarkdownV2",
    )

    # Build notify callback for improvement loop progress
    async def _notify_progress(msg: str) -> None:
        """Send improvement loop progress updates to the user."""
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"🔄 {msg}")
        except Exception:
            pass  # Don't let notification errors break the task

    # Emit task started event
    event_bus: EventBus | None = context.bot_data.get("event_bus")
    if event_bus:
        await event_bus.emit(
            EventType.TASK_STARTED,
            project=project,
            message=f"Task started: {intent.action} – {(intent.prompt or intent.raw_message)[:80]}",
            source="bot",
            task_id=task_id,
            action=intent.action,
        )

    # Route and execute (with improvement loop for code changes)
    try:
        result = await router.route(intent, task_id, notify=_notify_progress)
        await tracker.complete(task_id, success=result.success, output=result.output)

        # Emit task completed/failed event
        if event_bus:
            etype = EventType.TASK_COMPLETED if result.success else EventType.TASK_FAILED
            await event_bus.emit(
                etype,
                project=project,
                message=f"Task {intent.action}: {'OK' if result.success else 'FAILED'}",
                source="bot",
                task_id=task_id,
                action=intent.action,
                pr_url=result.pr_url or "",
            )

        # Store full output for "Ver detalle" button
        context.bot_data[f"result:{task_id}"] = {
            "project": project,
            "action": intent.action,
            "success": result.success,
            "output": result.output,
            "pr_url": result.pr_url,
        }

        # Generate 2-line summary via Gemini
        gemini: GeminiProvider = context.bot_data["gemini"]
        summary = await _summarize_output(result.output, intent.action, gemini)

        # If there's a PR URL, prepend it
        if result.pr_url:
            summary = f"PR: {result.pr_url}\n{summary}"

        # Send summary with MarkdownV2; fallback to plain text if formatting fails
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_task_summary(project, intent.action, result.success, summary, task_id),
                parse_mode="MarkdownV2",
                reply_markup=detail_button(task_id),
            )
        except Exception:
            # MarkdownV2 escape failed – send plain text
            icon = "OK" if result.success else "FALLO"
            plain = f"[{icon}] {project} > {intent.action}\n\n{summary}"
            await context.bot.send_message(
                chat_id=chat_id,
                text=plain,
                reply_markup=detail_button(task_id),
            )

    except Exception as e:
        logger.exception("Task execution failed: %s", task_id)
        await tracker.complete(task_id, success=False, output=str(e))
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=format_error(f"Fallo al ejecutar: {e}"),
                parse_mode="MarkdownV2",
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Error ejecutando tarea: {e}",
            )


# ── Callback query handler (inline keyboards) ───────────────────────────────


@authorized
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard presses."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    router: ActionRouter = context.bot_data["router"]
    tracker: TaskTracker = context.bot_data["tracker"]

    # ── Detail: show full output ─────────────────────────────────────────

    if data.startswith("detail:"):
        task_id = data.split(":", 1)[1]
        stored = context.bot_data.get(f"result:{task_id}")
        if not stored:
            await query.edit_message_text("Resultado no disponible (expirado).")
            return
        try:
            full_text = format_task_result(
                stored["project"], stored["action"], stored["success"], stored["output"]
            )
            if len(full_text) > 4000:
                full_text = full_text[:4000]
            await query.edit_message_text(full_text, parse_mode="MarkdownV2")
        except Exception:
            # MarkdownV2 failed – send as plain text
            output = stored["output"]
            if len(output) > 3800:
                output = output[:3800] + "\n... (truncado)"
            status = "OK" if stored["success"] else "ERROR"
            plain = f"[{status}] {stored['project']} / {stored['action']}\n\n{output}"
            await query.edit_message_text(plain)
        return

    # ── Confirm/cancel ────────────────────────────────────────────────────

    if data.startswith("confirm:"):
        task_id = data.split(":", 1)[1]
        intent = context.user_data.pop(f"pending:{task_id}", None)
        if not intent:
            await query.edit_message_text("Tarea expirada o no encontrada.")
            return
        await query.edit_message_text("Ejecutando...")
        await _execute_intent(update, context, intent, router, tracker)

    elif data.startswith("cancel:"):
        task_id = data.split(":", 1)[1]
        context.user_data.pop(f"pending:{task_id}", None)
        await tracker.complete(task_id, success=False, output="Cancelado por el usuario")
        await query.edit_message_text("Cancelado.")

    # ── Menu actions ──────────────────────────────────────────────────────

    elif data.startswith("menu:"):
        action = data.split(":", 1)[1]
        if action == "help":
            await query.edit_message_text(format_help(), parse_mode="MarkdownV2")
        elif action == "projects":
            registry: ProjectRegistry = context.bot_data["registry"]
            await query.edit_message_text(
                format_project_list(registry.all_projects()), parse_mode="MarkdownV2"
            )
        elif action == "tasks":
            active = await tracker.list_active()
            if not active:
                await query.edit_message_text("No hay tareas activas.")
            else:
                lines = ["*Tareas activas*\n"]
                for t in active:
                    lines.append(
                        f"  `{t['id'][:8]}` — {t['project']} / {t['action']} ({t['status']})"
                    )
                await query.edit_message_text("\n".join(lines), parse_mode="MarkdownV2")
        elif action == "mesh":
            mesh: AgentMesh = context.bot_data["agent_mesh"]
            status = mesh.status_summary()
            await query.edit_message_text(format_mesh_status(status), parse_mode="MarkdownV2")
        elif action == "casa":
            ha: HomeAssistantExecutor | None = context.bot_data.get("ha_executor")
            if not ha:
                await query.edit_message_text("Domotica no configurada.")
                return
            result = await ha.get_all_states()
            if result.success:
                await query.edit_message_text(
                    format_domotica_status(result.output),
                    parse_mode="MarkdownV2",
                    reply_markup=domotica_menu(),
                )
            else:
                await query.edit_message_text(
                    f"HA error: {result.output}", reply_markup=domotica_menu()
                )

    # ── Domotica quick actions ────────────────────────────────────────────

    elif data.startswith("casa:"):
        ha: HomeAssistantExecutor | None = context.bot_data.get("ha_executor")
        if not ha:
            await query.edit_message_text("Domotica no configurada.")
            return

        casa_action = data.split(":", 1)[1]

        if casa_action == "status":
            result = await ha.get_all_states()
            await query.edit_message_text(
                format_domotica_status(result.output) if result.success else f"Error: {result.output}",
                parse_mode="MarkdownV2" if result.success else None,
                reply_markup=domotica_menu(),
            )
        elif casa_action == "scenes":
            result = await ha.list_scenes()
            await query.edit_message_text(
                result.output, reply_markup=domotica_menu(),
            )
        elif casa_action == "automations":
            result = await ha.list_automations()
            await query.edit_message_text(
                result.output, reply_markup=domotica_menu(),
            )
        elif casa_action == "lights_on":
            result = await ha.call_service("light", "turn_on")
            await query.edit_message_text(
                "Todas las luces encendidas" if result.success else f"Error: {result.output}",
                reply_markup=domotica_menu(),
            )
        elif casa_action == "lights_off":
            result = await ha.call_service("light", "turn_off")
            await query.edit_message_text(
                "Todas las luces apagadas" if result.success else f"Error: {result.output}",
                reply_markup=domotica_menu(),
            )


# ── Photo handler (with or without caption) ──────────────────────────────────


@authorized
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos: with caption → parse intent + attach image.
    Without caption → Gemini Vision analyzes the image directly.
    """
    caption = update.effective_message.caption or ""
    photo = update.effective_message.photo[-1]
    file = await photo.get_file()
    photo_bytes = bytes(await file.download_as_bytearray())

    gemini: GeminiProvider = context.bot_data["gemini"]
    parser: IntentParser = context.bot_data["parser"]
    registry: ProjectRegistry = context.bot_data["registry"]
    router: ActionRouter = context.bot_data["router"]
    tracker: TaskTracker = context.bot_data["tracker"]
    user_id = update.effective_user.id if update.effective_user else None

    if caption:
        # Photo + text → parse intent and attach image for Cursor/Claude
        try:
            intent = await parser.parse(caption, registry.project_names(), user_id=user_id)
            intent.image_data = photo_bytes
        except Exception as e:
            logger.exception("Intent parsing failed for photo")
            await update.effective_message.reply_text(format_error(str(e)), parse_mode="MarkdownV2")
            return
        await _execute_intent(update, context, intent, router, tracker)
    else:
        # Photo sin texto → Gemini Vision analiza directamente
        status_msg = await update.effective_message.reply_text("Analizando imagen...")
        try:
            analysis = await gemini.generate_multimodal(
                prompt=(
                    "Analyze this image in detail. If it's a screenshot of code, explain the code, "
                    "any errors or bugs visible, and suggest fixes. If it's a UI screenshot, describe it. "
                    "If it's anything else, describe what you see. Respond in Spanish."
                ),
                media_data=photo_bytes,
                mime_type="image/jpeg",
                temperature=0.4,
                max_tokens=4096,
            )
            if analysis:
                # Truncate for Telegram limit
                if len(analysis) > 3800:
                    analysis = analysis[:3800] + "\n\n... (truncado)"
                await status_msg.edit_text(analysis)
            else:
                await status_msg.edit_text("No pude analizar la imagen.")
        except Exception as e:
            logger.exception("Photo analysis failed")
            await status_msg.edit_text(f"Error analizando imagen: {e}")


# ── Video handler ─────────────────────────────────────────────────────────────


@authorized
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages and video notes (round videos).

    Gemini 2.5 supports inline video analysis up to ~10MB.
    For larger videos, we extract a frame or report size limit.
    """
    video = update.effective_message.video or update.effective_message.video_note
    if not video:
        return

    caption = update.effective_message.caption or ""
    gemini: GeminiProvider = context.bot_data["gemini"]

    # Telegram file size limit for bots: 20MB download
    file_size = video.file_size or 0
    if file_size > 15 * 1024 * 1024:  # 15MB safety limit for Gemini inline
        await update.effective_message.reply_text(
            f"Video demasiado grande ({file_size // (1024*1024)}MB). "
            "Maximo ~15MB para analisis. Intenta con un video mas corto."
        )
        return

    status_msg = await update.effective_message.reply_text("Descargando y analizando video...")

    try:
        file = await video.get_file()
        video_bytes = bytes(await file.download_as_bytearray())

        # Detect mime type
        mime = getattr(video, "mime_type", "video/mp4") or "video/mp4"

        prompt = caption or (
            "Analyze this video in detail. Describe what you see, any relevant information, "
            "code being shown, UI interactions, bugs, etc. If it's a screen recording of code, "
            "explain what's happening and any issues. Respond in Spanish."
        )

        analysis = await gemini.generate_multimodal(
            prompt=prompt,
            media_data=video_bytes,
            mime_type=mime,
            temperature=0.4,
            max_tokens=4096,
        )

        if analysis:
            if len(analysis) > 3800:
                analysis = analysis[:3800] + "\n\n... (truncado)"
            await status_msg.edit_text(analysis)
        else:
            await status_msg.edit_text("No pude analizar el video.")

    except Exception as e:
        logger.exception("Video analysis failed")
        await status_msg.edit_text(f"Error analizando video: {e}")


# ── Voice / Audio handler ─────────────────────────────────────────────────────


@authorized
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voice messages and audio files – transcribe with Gemini, then parse intent."""
    voice = update.effective_message.voice or update.effective_message.audio
    if not voice:
        return

    voice_parser: VoiceParser | None = context.bot_data.get("voice_parser")
    if not voice_parser:
        await update.effective_message.reply_text(
            "Transcripcion de voz no disponible. Configura GEMINI_API_KEY."
        )
        return

    file = await voice.get_file()
    audio_bytes = bytes(await file.download_as_bytearray())

    # Detect mime type from audio file
    mime_type = "audio/ogg"  # default for Telegram voice
    if update.effective_message.audio:
        # Audio document (mp3, m4a, etc.)
        audio_obj = update.effective_message.audio
        mime_type = audio_obj.mime_type or "audio/mpeg"
        file_name = audio_obj.file_name or ""
        if file_name:
            import os
            ext = os.path.splitext(file_name)[1]
            mime_type = VoiceParser.mime_type_from_extension(ext) if ext else mime_type

    status_msg = await update.effective_message.reply_text("Transcribiendo audio...")

    raw_text, cleaned_text = await voice_parser.transcribe_and_parse(
        audio_data=audio_bytes,
        mime_type=mime_type,
    )

    if not cleaned_text:
        await status_msg.edit_text("No pude transcribir el audio. Intenta de nuevo o envia texto.")
        return

    await status_msg.edit_text(f'Transcripcion: "{cleaned_text}"\n\nProcesando...')

    # Parse the transcribed text as a regular message
    parser: IntentParser = context.bot_data["parser"]
    registry: ProjectRegistry = context.bot_data["registry"]
    router: ActionRouter = context.bot_data["router"]
    tracker: TaskTracker = context.bot_data["tracker"]

    user_id = update.effective_user.id if update.effective_user else None
    try:
        intent = await parser.parse(cleaned_text, registry.project_names(), user_id=user_id)
    except Exception as e:
        logger.exception("Intent parsing failed for voice")
        await update.effective_message.reply_text(format_error(str(e)), parse_mode="MarkdownV2")
        return

    if intent.project:
        project_info = registry.resolve(intent.project)
        if not project_info:
            await update.effective_message.reply_text(
                format_error(f"Proyecto '{intent.project}' no encontrado."),
                parse_mode="MarkdownV2",
            )
            return

    if intent.action in DESTRUCTIVE_ACTIONS:
        task_id = str(uuid.uuid4())
        await tracker.create(task_id, intent, status="pending_confirmation")
        context.user_data[f"pending:{task_id}"] = intent
        await update.effective_message.reply_text(
            f"Confirmar *{intent.action}* en `{intent.project}`?",
            parse_mode="MarkdownV2",
            reply_markup=confirm_keyboard(task_id),
        )
        return

    await _execute_intent(update, context, intent, router, tracker)


# ── Document handler (files: audio, images, video) ───────────────────────────


@authorized
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document attachments (audio files, images, videos sent as documents)."""
    doc = update.effective_message.document
    if not doc:
        return

    mime = doc.mime_type or ""
    file_name = doc.file_name or ""
    caption = update.effective_message.caption or ""

    # Route by type
    if mime.startswith("audio/"):
        # Audio file sent as document → transcribe
        voice_parser: VoiceParser | None = context.bot_data.get("voice_parser")
        if not voice_parser:
            await update.effective_message.reply_text("Transcripcion no disponible.")
            return

        status_msg = await update.effective_message.reply_text(f"Transcribiendo {file_name}...")
        file = await doc.get_file()
        data = bytes(await file.download_as_bytearray())

        import os
        ext = os.path.splitext(file_name)[1] if file_name else ""
        audio_mime = VoiceParser.mime_type_from_extension(ext) if ext else mime

        raw, cleaned = await voice_parser.transcribe_and_parse(data, audio_mime)
        if cleaned:
            await status_msg.edit_text(f'Transcripcion de {file_name}:\n\n"{cleaned}"')
        else:
            await status_msg.edit_text("No pude transcribir el archivo de audio.")

    elif mime.startswith("image/"):
        # Image sent as document → analyze with Gemini Vision
        gemini: GeminiProvider = context.bot_data["gemini"]
        status_msg = await update.effective_message.reply_text("Analizando imagen...")
        file = await doc.get_file()
        data = bytes(await file.download_as_bytearray())

        prompt = caption or "Describe and analyze this image in detail. Respond in Spanish."
        analysis = await gemini.generate_multimodal(prompt, data, mime, temperature=0.4)
        if analysis:
            if len(analysis) > 3800:
                analysis = analysis[:3800] + "\n... (truncado)"
            await status_msg.edit_text(analysis)
        else:
            await status_msg.edit_text("No pude analizar la imagen.")

    elif mime.startswith("video/"):
        # Video sent as document
        if (doc.file_size or 0) > 15 * 1024 * 1024:
            await update.effective_message.reply_text("Video demasiado grande (max ~15MB).")
            return

        gemini: GeminiProvider = context.bot_data["gemini"]
        status_msg = await update.effective_message.reply_text(f"Analizando video {file_name}...")
        file = await doc.get_file()
        data = bytes(await file.download_as_bytearray())

        prompt = caption or "Analyze this video. Describe what you see. Respond in Spanish."
        analysis = await gemini.generate_multimodal(prompt, data, mime, temperature=0.4)
        if analysis:
            if len(analysis) > 3800:
                analysis = analysis[:3800] + "\n... (truncado)"
            await status_msg.edit_text(analysis)
        else:
            await status_msg.edit_text("No pude analizar el video.")

    else:
        await update.effective_message.reply_text(
            f"Archivo recibido ({mime}). Solo proceso: audio, imagenes y videos."
        )


# ── Register all handlers ────────────────────────────────────────────────────


def register_handlers(app: Application) -> None:
    """Attach all handlers to the Telegram application."""
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("casa", cmd_casa))
    app.add_handler(CommandHandler("mesh", cmd_mesh))

    # Callback queries (inline keyboards)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Media handlers (order matters – more specific first)
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Text (catch-all, last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
