"""Main entry point – self-hosted orchestrator with multi-agent mesh.

No Railway needed. Runs on any of your PCs with Cloudflare Tunnel for webhooks.

    python -m src.main               # production (webhook mode via CF Tunnel)
    python -m src.main --polling      # development (Telegram polling, no tunnel needed)

Now includes:
- GitHub monitor (push, PR, deployment events)
- Vercel monitor (deployment status)
- Project monitor (local git state)
- Real-time dashboard at /dashboard
- Proactive notifications (Telegram + WhatsApp)
- Event bus for all system events
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, WebSocket
from telegram import Update
from telegram.ext import Application
import uvicorn

from src.bot.handlers import register_handlers
from src.bot.voice_parser import VoiceParser
from src.bot.wa_bridge_handler import WABridgeHandler
from src.bot.whatsapp_handlers import WhatsAppHandler
from src.config import get_settings, Settings
from src.dashboard import router as dashboard_router
from src.events import EventBus, EventStore, EventType
from src.executors.agent_mesh import AgentMesh
from src.executors.cursor_executor import CursorExecutor
from src.executors.homeassistant_executor import HomeAssistantExecutor
from src.executors.improvement_loop import ImprovementLoop, ImprovementConfig
from src.monitors.github import GitHubMonitor
from src.monitors.projects import ProjectMonitor
from src.monitors.vercel import VercelMonitor
from src.notifier import ProactiveNotifier
from src.orchestrator.intent_parser import IntentParser
from src.orchestrator.project_registry import ProjectRegistry
from src.orchestrator.router import ActionRouter
from src.orchestrator.task_tracker import TaskTracker
from src.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)


def build_components(settings: Settings) -> dict:
    """Instantiate all components."""
    # ── Gemini (intent parsing + voice transcription) ─────────────────────
    gemini = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
    )

    # ── Project registry ──────────────────────────────────────────────────
    registry = ProjectRegistry(settings.projects_file)

    # ── Intent parser (Gemini) ────────────────────────────────────────────
    parser = IntentParser(gemini=gemini)

    # ── Voice parser (Gemini multimodal) ──────────────────────────────────
    voice_parser = VoiceParser(gemini=gemini) if settings.gemini_configured else None

    # ── Task tracker (SQLite) ─────────────────────────────────────────────
    tracker = TaskTracker(db_path=settings.db_path)

    # ── Cursor executor (Cloud Agents API – company paid) ──────────────────
    cursor_executor = None
    if settings.cursor_api_key and not settings.cursor_api_key.startswith("your-"):
        cursor_executor = CursorExecutor(
            api_key=settings.cursor_api_key,
            default_model=None,  # Let Cursor auto-pick the best model
        )
        logger.info("Cursor Cloud Agents API enabled")

    # ── Agent mesh (multi-PC) ─────────────────────────────────────────────
    agent_mesh = AgentMesh(ws_secret=settings.ws_secret)

    # ── Home Assistant (domotica) ─────────────────────────────────────────
    ha_executor = None
    if settings.ha_enabled:
        ha_executor = HomeAssistantExecutor(
            url=settings.ha_url,
            token=settings.ha_token,
        )

    # ── Improvement loop (autonomous self-improvement after tasks) ──────
    improvement_loop = ImprovementLoop(
        agent_mesh=agent_mesh,
        cursor_executor=cursor_executor,
        config=ImprovementConfig(
            max_iterations=3,      # Up to 3 review-improve cycles
            run_tests=True,        # Always run tests
            improve_quality=True,  # Fix lint, types, docs
            add_tests=True,        # Write missing tests
            fix_lint=True,         # Auto-fix lint issues
            timeout_per_step=300,  # 5 min per improvement step
        ),
    )
    logger.info("Improvement loop enabled: max 3 iterations, auto-tests, auto-quality")

    # ── Event system ──────────────────────────────────────────────────────
    event_store = EventStore(db_path="data/events.db")
    event_bus = EventBus(store=event_store)

    # ── Proactive notifier (Telegram + WhatsApp) ──────────────────────────
    notifier = ProactiveNotifier(
        telegram_token=settings.telegram_bot_token,
        telegram_chat_id=settings.notification_telegram_chat_id,
        wa_bridge_url=settings.wa_bridge_url,
        wa_number=settings.notification_whatsapp_number,
        enabled=settings.notification_enabled,
    )

    # Subscribe notifier to events
    event_bus.subscribe(notifier.notify_event)

    # ── Build repo map from registry ─────────────────────────────────────
    all_projects_dict = registry.all_projects()  # {name: info_dict}
    repos = {}
    for name, info in all_projects_dict.items():
        if info.get("repo"):
            repos[name] = info["repo"]

    # ── GitHub monitor ────────────────────────────────────────────────────
    github_monitor = None
    if settings.github_enabled:
        github_monitor = GitHubMonitor(
            token=settings.github_token,
            event_bus=event_bus,
            repos=repos,
            poll_interval=settings.github_poll_interval,
            webhook_secret=settings.github_webhook_secret,
        )

    # ── Vercel monitor ────────────────────────────────────────────────────
    vercel_monitor = None
    if settings.vercel_enabled:
        vercel_monitor = VercelMonitor(
            token=settings.vercel_token,
            event_bus=event_bus,
            project_repos=repos,
            poll_interval=settings.vercel_poll_interval,
            team_id=settings.vercel_team_id,
        )

    # ── Project monitor (local git) ───────────────────────────────────────
    all_projects = {name: {**info, "_name": name} for name, info in all_projects_dict.items()}
    project_monitor = ProjectMonitor(
        event_bus=event_bus,
        projects=all_projects,
        poll_interval=settings.project_monitor_interval,
    )

    # ── Router (smart: mesh + Cursor + HA + improvement + Gemini) ────────
    router = ActionRouter(
        registry=registry,
        cursor_executor=cursor_executor,
        agent_mesh=agent_mesh,
        ha_executor=ha_executor,
        improvement_loop=improvement_loop,
        gemini=gemini,
    )

    return {
        "settings": settings,
        "gemini": gemini,
        "registry": registry,
        "parser": parser,
        "voice_parser": voice_parser,
        "tracker": tracker,
        "router": router,
        "cursor_executor": cursor_executor,
        "agent_mesh": agent_mesh,
        "ha_executor": ha_executor,
        "improvement_loop": improvement_loop,
        "event_bus": event_bus,
        "event_store": event_store,
        "notifier": notifier,
        "github_monitor": github_monitor,
        "vercel_monitor": vercel_monitor,
        "project_monitor": project_monitor,
    }


# ── FastAPI app ──────────────────────────────────────────────────────────────


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    components = build_components(settings)
    tg_app: Application | None = None
    wa_handler: WhatsAppHandler | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal tg_app, wa_handler
        app.state.start_time = time.time()

        # ── Initialize stores ─────────────────────────────────────────────
        await components["tracker"].initialize()
        await components["event_store"].initialize()

        # ── Telegram ──────────────────────────────────────────────────────
        if settings.telegram_enabled:
            tg_app = Application.builder().token(settings.telegram_bot_token).build()
            for k, v in components.items():
                tg_app.bot_data[k] = v
            register_handlers(tg_app)
            await tg_app.initialize()
            await tg_app.start()
            if settings.webhook_url:
                await tg_app.bot.set_webhook(f"{settings.webhook_url}/telegram/webhook")
            app.state.tg_app = tg_app

        # ── WhatsApp ──────────────────────────────────────────────────────
        if settings.whatsapp_enabled:
            wa_handler = WhatsAppHandler(
                token=settings.whatsapp_token,
                phone_number_id=settings.whatsapp_phone_number_id,
                verify_token=settings.whatsapp_verify_token,
                allowed_numbers=settings.allowed_whatsapp_numbers,
                parser=components["parser"],
                registry=components["registry"],
                router=components["router"],
                tracker=components["tracker"],
                voice_parser=components["voice_parser"],
                gemini=components["gemini"],
            )
            app.state.wa_handler = wa_handler

        app.state.components = components

        # ── Start background monitors ─────────────────────────────────────
        bg_tasks = []

        if components["github_monitor"]:
            bg_tasks.append(asyncio.create_task(
                components["github_monitor"].start_polling()
            ))
            logger.info("GitHub monitor started")

        if components["vercel_monitor"]:
            bg_tasks.append(asyncio.create_task(
                components["vercel_monitor"].start_polling()
            ))
            logger.info("Vercel monitor started")

        bg_tasks.append(asyncio.create_task(
            components["project_monitor"].start_monitoring()
        ))
        logger.info("Project monitor started")

        # ── Emit bot started event ────────────────────────────────────────
        channels = []
        if settings.telegram_enabled:
            channels.append("Telegram")
        if settings.whatsapp_enabled:
            channels.append("WhatsApp")

        await components["event_bus"].emit(
            EventType.BOT_STARTED,
            project="system",
            message=f"Sierra Bot started | Channels: {', '.join(channels) or 'none'}",
            source="bot",
        )

        logger.info(
            "Orchestrator started | Self-hosted | Channels: %s | AI: Gemini %s + Cursor | "
            "Projects: %d | GitHub: %s | Vercel: %s | Dashboard: /dashboard",
            ", ".join(channels) or "none",
            settings.gemini_model,
            len(components["registry"].project_names()),
            "ON" if components["github_monitor"] else "OFF",
            "ON" if components["vercel_monitor"] else "OFF",
        )

        yield

        # ── Cleanup ───────────────────────────────────────────────────────
        for task in bg_tasks:
            task.cancel()

        if tg_app:
            await tg_app.stop()
            await tg_app.shutdown()
        if wa_handler:
            await wa_handler.close()
        await components["tracker"].close()
        await components["event_store"].close()
        await components["gemini"].close()
        await components["notifier"].close()
        if components["cursor_executor"]:
            await components["cursor_executor"].close()
        if components["ha_executor"]:
            await components["ha_executor"].close()
        if components["github_monitor"]:
            await components["github_monitor"].close()
        if components["vercel_monitor"]:
            await components["vercel_monitor"].close()
        await components["project_monitor"].close()

    app = FastAPI(title="Sierra Bot – Cursor Orchestrator", lifespan=lifespan)

    # ── CORS (allow remote dashboard at guillesierra.com) ─────────────────
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Dashboard + API routes ────────────────────────────────────────────
    app.include_router(dashboard_router)

    # ── Telegram webhook ──────────────────────────────────────────────────

    @app.post("/telegram/webhook")
    async def telegram_webhook(request: Request):
        tg = getattr(request.app.state, "tg_app", None)
        if not tg:
            return {"error": "Telegram not configured"}
        update = Update.de_json(await request.json(), tg.bot)
        await tg.process_update(update)
        return {"ok": True}

    # ── WhatsApp webhooks ─────────────────────────────────────────────────

    @app.get("/whatsapp/webhook")
    async def wa_verify(request: Request):
        wa = getattr(request.app.state, "wa_handler", None)
        if not wa:
            return Response(status_code=404)
        return await wa.verify_webhook(request)

    @app.post("/whatsapp/webhook")
    async def wa_incoming(request: Request):
        wa = getattr(request.app.state, "wa_handler", None)
        if not wa:
            return {"error": "WhatsApp not configured"}
        return await wa.handle_webhook(request)

    # ── GitHub webhook ────────────────────────────────────────────────────

    @app.post("/webhooks/github")
    async def github_webhook(request: Request):
        gh = app.state.components.get("github_monitor")
        if not gh:
            return {"error": "GitHub monitor not configured"}
        return await gh.handle_webhook(request)

    # ── Vercel webhook ────────────────────────────────────────────────────

    @app.post("/webhooks/vercel")
    async def vercel_webhook(request: Request):
        vc = app.state.components.get("vercel_monitor")
        if not vc:
            return {"error": "Vercel monitor not configured"}
        return await vc.handle_webhook(request)

    # ── Agent mesh WebSocket ──────────────────────────────────────────────

    @app.websocket("/ws/agent")
    async def agent_ws(ws: WebSocket):
        mesh: AgentMesh = app.state.components["agent_mesh"]
        await mesh.handle_agent_connection(ws)

    # ── Health / status ───────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        c = app.state.components
        mesh: AgentMesh = c["agent_mesh"]
        gh = c.get("github_monitor")
        vc = c.get("vercel_monitor")
        pm = c.get("project_monitor")

        return {
            "status": "ok",
            "mode": "self-hosted",
            "channels": {
                "telegram": bool(getattr(app.state, "tg_app", None)),
                "whatsapp": bool(getattr(app.state, "wa_handler", None)),
            },
            "ai": {
                "intent_parser": f"Gemini {c['settings'].gemini_model}",
                "code_changes": "Cursor Opus 4.6 Max" if c["cursor_executor"] else "Claude Code CLI",
                "voice": "Gemini multimodal" if c["voice_parser"] else "disabled",
            },
            "monitors": {
                "github": gh.status() if gh else {"enabled": False},
                "vercel": vc.status() if vc else {"enabled": False},
                "projects": pm.status_summary() if pm else {"enabled": False},
            },
            "domotica": {
                "enabled": bool(c["ha_executor"]),
                "platform": "Home Assistant" if c["ha_executor"] else "not configured",
            },
            "mesh": mesh.status_summary(),
            "projects": len(c["registry"].project_names()),
            "dashboard": "/dashboard",
        }

    return app


# ── Polling mode (development) ───────────────────────────────────────────────


async def run_polling(settings: Settings) -> None:
    """Polling mode: Telegram polling + FastAPI for WebSocket agent mesh.

    Runs both in the same asyncio loop. No Cloudflare Tunnel needed.
    """
    components = build_components(settings)
    await components["tracker"].initialize()
    await components["event_store"].initialize()

    if not settings.telegram_enabled:
        logger.error("Polling mode needs TELEGRAM_BOT_TOKEN")
        return

    tg_app = Application.builder().token(settings.telegram_bot_token).build()
    for k, v in components.items():
        tg_app.bot_data[k] = v
    register_handlers(tg_app)

    # FastAPI for WebSocket agent mesh + health + WA bridge + dashboard
    app = FastAPI()

    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.start_time = time.time()
    app.state.components = components
    app.state.tg_app = tg_app  # Expose to dashboard in polling mode
    wa_handler = None
    wa_bridge = None

    # ── Dashboard ─────────────────────────────────────────────────────────
    app.include_router(dashboard_router)

    @app.websocket("/ws/agent")
    async def agent_ws(ws: WebSocket):
        mesh: AgentMesh = components["agent_mesh"]
        await mesh.handle_agent_connection(ws)

    # ── GitHub webhook ────────────────────────────────────────────────────

    @app.post("/webhooks/github")
    async def github_wh(request: Request):
        gh = components.get("github_monitor")
        if not gh:
            return {"error": "Not configured"}
        return await gh.handle_webhook(request)

    # ── Vercel webhook ────────────────────────────────────────────────────

    @app.post("/webhooks/vercel")
    async def vercel_wh(request: Request):
        vc = components.get("vercel_monitor")
        if not vc:
            return {"error": "Not configured"}
        return await vc.handle_webhook(request)

    # ── WhatsApp Web Bridge (Baileys – personal number) ──────────────────
    if settings.wa_bridge_enabled:
        wa_bridge = WABridgeHandler(
            bridge_url=settings.wa_bridge_url,
            allowed_numbers=settings.allowed_whatsapp_numbers,
            parser=components["parser"],
            registry=components["registry"],
            router=components["router"],
            tracker=components["tracker"],
            voice_parser=components["voice_parser"],
            gemini=components["gemini"],
        )
        app.state.wa_bridge = wa_bridge

        @app.post("/wa-bridge/incoming")
        async def wa_bridge_incoming(request: Request):
            data = await request.json()
            return await wa_bridge.handle_incoming(data)

        logger.info("WhatsApp Web Bridge handler registered at /wa-bridge/incoming")

    # ── WhatsApp Business API (Meta – optional legacy) ───────────────────
    if settings.whatsapp_enabled:
        wa_handler = WhatsAppHandler(
            token=settings.whatsapp_token,
            phone_number_id=settings.whatsapp_phone_number_id,
            verify_token=settings.whatsapp_verify_token,
            allowed_numbers=settings.allowed_whatsapp_numbers,
            parser=components["parser"],
            registry=components["registry"],
            router=components["router"],
            tracker=components["tracker"],
            voice_parser=components["voice_parser"],
            gemini=components["gemini"],
        )

        @app.get("/whatsapp/webhook")
        async def wa_v(request: Request):
            return await wa_handler.verify_webhook(request)

        @app.post("/whatsapp/webhook")
        async def wa_i(request: Request):
            return await wa_handler.handle_webhook(request)

    @app.get("/health")
    async def health():
        mesh: AgentMesh = components["agent_mesh"]
        gh = components.get("github_monitor")
        vc = components.get("vercel_monitor")
        pm = components.get("project_monitor")
        # Check WA bridge connectivity
        wa_bridge_status = "disabled"
        if wa_bridge:
            try:
                import httpx as hx
                async with hx.AsyncClient(timeout=3.0) as c:
                    r = await c.get(f"{settings.wa_bridge_url}/health")
                    wa_bridge_status = r.json().get("status", "unknown")
            except Exception:
                wa_bridge_status = "unreachable"
        return {
            "status": "ok",
            "mesh": mesh.status_summary(),
            "whatsapp_bridge": wa_bridge_status,
            "monitors": {
                "github": gh.status() if gh else {"enabled": False},
                "vercel": vc.status() if vc else {"enabled": False},
                "projects": pm.status_summary() if pm else {"enabled": False},
            },
            "dashboard": "/dashboard",
        }

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    # Prevent uvicorn from installing its own signal handlers (conflicts with telegram)
    server.install_signal_handlers = lambda: None

    logger.info(
        "Polling mode | Port %d | Dashboard: http://localhost:%d/dashboard",
        settings.port, settings.port,
    )

    stop_event = asyncio.Event()
    bg_tasks = []

    async def _run_uvicorn():
        try:
            await server.serve()
        except SystemExit:
            logger.warning("Uvicorn exited")
        except Exception:
            logger.exception("Uvicorn error")
        finally:
            stop_event.set()

    async def _run_telegram():
        try:
            async with tg_app:
                await tg_app.start()
                await tg_app.updater.start_polling(drop_pending_updates=True)
                logger.info("Telegram polling started for @%s", (await tg_app.bot.get_me()).username)
                # Wait until stop is requested
                await stop_event.wait()
                await tg_app.updater.stop()
                await tg_app.stop()
        except Exception:
            logger.exception("Telegram error")
        finally:
            stop_event.set()

    async def _run_monitors():
        """Start all background monitors."""
        try:
            tasks = []
            if components["github_monitor"]:
                tasks.append(asyncio.create_task(
                    components["github_monitor"].start_polling()
                ))
                logger.info("GitHub monitor started")

            if components["vercel_monitor"]:
                tasks.append(asyncio.create_task(
                    components["vercel_monitor"].start_polling()
                ))
                logger.info("Vercel monitor started")

            tasks.append(asyncio.create_task(
                components["project_monitor"].start_monitoring()
            ))
            logger.info("Project monitor started")

            # Emit startup event
            await components["event_bus"].emit(
                EventType.BOT_STARTED,
                project="system",
                message="Sierra Bot started in polling mode",
                source="bot",
            )

            bg_tasks.extend(tasks)
            await stop_event.wait()

            for t in tasks:
                t.cancel()
        except Exception:
            logger.exception("Monitor error")

    async def _cleanup():
        await stop_event.wait()
        await asyncio.sleep(1)
        await components["tracker"].close()
        await components["event_store"].close()
        await components["gemini"].close()
        await components["notifier"].close()
        if components["cursor_executor"]:
            await components["cursor_executor"].close()
        if components["ha_executor"]:
            await components["ha_executor"].close()
        if components["github_monitor"]:
            await components["github_monitor"].close()
        if components["vercel_monitor"]:
            await components["vercel_monitor"].close()
        await components["project_monitor"].close()
        if wa_handler:
            await wa_handler.close()
        if wa_bridge:
            await wa_bridge.close()
        logger.info("Cleanup done")

    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: stop_event.set())

    # Run everything concurrently
    await asyncio.gather(
        _run_uvicorn(),
        _run_telegram(),
        _run_monitors(),
        _cleanup(),
        return_exceptions=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Sierra Bot – Cursor Orchestrator (self-hosted)")
    parser.add_argument("--polling", action="store_true", help="Polling mode (dev)")
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.polling:
        asyncio.run(run_polling(settings))
    else:
        app = create_app(settings)
        uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
