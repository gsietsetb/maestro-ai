"""Slash command parser – fast, deterministic command routing without LLM.

Parses /command arg --flag value syntax, resolves handlers via a registry
pattern, and returns structured CommandResult objects.  Falls through to
None when the message is not a slash command so the Gemini intent parser
takes over (backward-compatible).
"""

from __future__ import annotations

import logging
import shlex
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, ClassVar

from src.orchestrator.command_registry import CommandRegistry

logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class ParsedCommand:
    """Result of parsing a slash command string."""

    name: str
    subcommand: str | None
    args: list[str]
    flags: dict[str, str]
    raw: str


@dataclass
class CommandResult:
    """Universal response contract for every slash command."""

    run_id: str
    status: str                       # queued, running, done, error, delegate
    progress: float                   # 0.0 to 1.0
    summary: str
    artifacts: list[dict] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)


@dataclass
class CommandContext:
    """Dependencies available to command handlers."""

    registry: CommandRegistry
    task_tracker: Any = None       # TaskTracker
    ha_executor: Any = None        # HomeAssistantExecutor
    gemini: Any = None             # GeminiProvider
    agent_mesh: Any = None         # AgentMesh
    project_registry: Any = None   # ProjectRegistry


# Type alias for handler functions
HandlerFn = Callable[[ParsedCommand, CommandContext], Awaitable[CommandResult]]

# Sentinel agent_id used to signal "delegate to NLU intent parser"
DELEGATE_SENTINEL = "__delegate_nlu__"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_id() -> str:
    return uuid.uuid4().hex[:12]


def _ok(summary: str, *, next_actions: list[str] | None = None,
        artifacts: list[dict] | None = None) -> CommandResult:
    """Shortcut for a successful result."""
    return CommandResult(
        run_id=_run_id(),
        status="done",
        progress=1.0,
        summary=summary,
        artifacts=artifacts or [],
        next_actions=next_actions or [],
    )


def _error(summary: str, *, next_actions: list[str] | None = None) -> CommandResult:
    """Shortcut for an error result."""
    return CommandResult(
        run_id=_run_id(),
        status="error",
        progress=1.0,
        summary=summary,
        next_actions=next_actions or ["/help"],
    )


def _queued(summary: str, run_id: str | None = None,
            next_actions: list[str] | None = None) -> CommandResult:
    """Shortcut for a queued/running result."""
    return CommandResult(
        run_id=run_id or _run_id(),
        status="queued",
        progress=0.0,
        summary=summary,
        next_actions=next_actions or [],
    )


def _delegate(text: str) -> CommandResult:
    """Signal that this text should be delegated to the NLU intent parser."""
    return CommandResult(
        run_id="",
        status="delegate",
        progress=0.0,
        summary=text,
    )


def _stub(feature: str, *, next_actions: list[str] | None = None) -> CommandResult:
    """Placeholder for features not yet implemented."""
    return _ok(
        f"{feature} — todavia no implementado. Proximamente.",
        next_actions=next_actions or ["/help"],
    )


def _tokenize(text: str) -> list[str]:
    """Split text into tokens, respecting quoted strings."""
    try:
        return shlex.split(text)
    except ValueError:
        # Unmatched quotes – fall back to simple whitespace split
        return text.split()


# ── Built-in command handlers ────────────────────────────────────────────────


async def _handle_help(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """List all commands or show category detail."""
    category = cmd.args[0] if cmd.args else None
    help_text = ctx.registry.format_help(category)
    return _ok(help_text)


async def _handle_ping(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """System health check."""
    lines: list[str] = ["Sistema operativo.\n"]

    # Check configured services
    checks = [
        ("Gemini", ctx.gemini and getattr(ctx.gemini, "configured", False)),
        ("Home Assistant", ctx.ha_executor is not None),
        ("Agent Mesh", ctx.agent_mesh and getattr(ctx.agent_mesh, "is_connected", False)),
        ("Task Tracker", ctx.task_tracker is not None),
        ("Project Registry", ctx.project_registry is not None),
    ]
    for name, ok in checks:
        icon = "\u2705" if ok else "\u274c"
        lines.append(f"  {icon} {name}")

    lines.append(f"\n  Timestamp: {datetime.now(timezone.utc).isoformat()}")
    return _ok("\n".join(lines), next_actions=["/help", "/status"])


async def _handle_run(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """Delegate free text to the NLU intent parser."""
    text = " ".join(cmd.args)
    if not text:
        return _error("Falta el texto: /run <texto>", next_actions=["/help"])
    return _delegate(text)


async def _handle_status(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """Check task progress."""
    if not ctx.task_tracker:
        return _error("Task tracker no disponible.")

    run_id = cmd.args[0] if cmd.args else None

    if not run_id:
        # Show all active tasks
        active = await ctx.task_tracker.list_active()
        if not active:
            return _ok("No hay tareas activas.", next_actions=["/help"])
        lines = ["Tareas activas:\n"]
        for t in active:
            lines.append(
                f"  {t['id'][:8]}  {t.get('project', '?')} / "
                f"{t['action']} ({t['status']})"
            )
        return _ok("\n".join(lines), next_actions=["/cancel <id>"])
    else:
        task = await ctx.task_tracker.get(run_id)
        if not task:
            # Try partial match
            recent = await ctx.task_tracker.list_recent(50)
            matches = [t for t in recent if t["id"].startswith(run_id)]
            if len(matches) == 1:
                task = matches[0]
            elif len(matches) > 1:
                ids = ", ".join(m["id"][:8] for m in matches)
                return _error(f"Multiples coincidencias: {ids}. Se mas especifico.")
            else:
                return _error(f"Tarea '{run_id}' no encontrada.", next_actions=["/status"])

        lines = [
            f"Tarea: {task['id'][:8]}",
            f"  Accion: {task['action']}",
            f"  Proyecto: {task.get('project', 'global')}",
            f"  Estado: {task['status']}",
            f"  Creada: {task.get('created_at', '?')}",
        ]
        if task.get("completed_at"):
            lines.append(f"  Completada: {task['completed_at']}")
        if task.get("output"):
            output = task["output"]
            if len(output) > 300:
                output = output[:300] + "..."
            lines.append(f"  Resultado: {output}")
        return _ok("\n".join(lines), next_actions=["/cancel " + task["id"][:8]])


async def _handle_cancel(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """Cancel a running task."""
    if not ctx.task_tracker:
        return _error("Task tracker no disponible.")
    run_id = cmd.args[0] if cmd.args else None
    if not run_id:
        return _error("Falta el run_id: /cancel <run_id>", next_actions=["/status"])

    task = await ctx.task_tracker.get(run_id)
    if not task:
        # Try partial match
        recent = await ctx.task_tracker.list_recent(50)
        matches = [t for t in recent if t["id"].startswith(run_id)]
        if len(matches) == 1:
            task = matches[0]
            run_id = task["id"]
        else:
            return _error(f"Tarea '{run_id}' no encontrada.", next_actions=["/status"])

    if task["status"] in ("completed", "failed", "cancelled"):
        return _error(
            f"Tarea ya finalizada ({task['status']}).",
            next_actions=["/retry " + run_id[:8]],
        )

    await ctx.task_tracker.update_status(run_id, "cancelled")
    return _ok(
        f"Tarea {run_id[:8]} cancelada.",
        next_actions=["/status", "/retry " + run_id[:8]],
    )


async def _handle_retry(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """Retry a failed task."""
    if not ctx.task_tracker:
        return _error("Task tracker no disponible.")
    run_id = cmd.args[0] if cmd.args else None
    if not run_id:
        return _error("Falta el run_id: /retry <run_id>", next_actions=["/status"])

    task = await ctx.task_tracker.get(run_id)
    if not task:
        return _error(f"Tarea '{run_id}' no encontrada.", next_actions=["/status"])

    if task["status"] not in ("failed", "cancelled"):
        return _error(
            f"Solo se pueden reintentar tareas fallidas o canceladas (actual: {task['status']}).",
            next_actions=["/status " + run_id[:8]],
        )

    # Re-delegate the original message through NLU
    original_msg = task.get("raw_message") or task.get("prompt") or ""
    if not original_msg:
        return _error("No se encontro el mensaje original para reintentar.")
    return _delegate(original_msg)


async def _handle_summarize(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """Summarize a task result or arbitrary text."""
    text = " ".join(cmd.args)
    if not text:
        return _error("Falta el texto o run_id: /summarize <run_id|texto>")

    # Check if it looks like a run_id (try to fetch from tracker)
    if ctx.task_tracker and len(text) <= 40 and " " not in text:
        task = await ctx.task_tracker.get(text)
        if task and task.get("output"):
            text = task["output"]

    if not ctx.gemini or not getattr(ctx.gemini, "configured", False):
        # Simple fallback: first 200 chars
        if len(text) > 200:
            return _ok(text[:200] + "...", next_actions=["/help"])
        return _ok(text, next_actions=["/help"])

    try:
        summary = await ctx.gemini.generate(
            prompt=(
                "Resume en 2-3 lineas breves este texto. "
                "Sin markdown ni emojis. En espanol, directo:\n\n"
                f"{text[:3000]}"
            ),
            temperature=0.3,
            max_tokens=400,
            disable_thinking=True,
        )
        if summary and len(summary.strip()) > 10:
            return _ok(summary.strip())
    except Exception as e:
        logger.warning("Summarize failed: %s", e)

    # Fallback
    return _ok(text[:300] + ("..." if len(text) > 300 else ""))


# ── Memory (stubs) ───────────────────────────────────────────────────────────


async def _handle_remember(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    text = " ".join(cmd.args)
    if not text:
        return _error("Falta el texto: /remember <texto>")
    return _stub("Memoria persistente", next_actions=["/forget", "/help memory"])


async def _handle_forget(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    topic = " ".join(cmd.args)
    if not topic:
        return _error("Falta el tema: /forget <tema>")
    return _stub("Borrado de memoria", next_actions=["/remember", "/help memory"])


# ── Settings (stubs) ─────────────────────────────────────────────────────────


async def _handle_set(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    sub = cmd.subcommand
    value = " ".join(cmd.args) if cmd.args else None

    if sub == "voice":
        if not value:
            return _error("Falta el nombre de voz: /set voice <nombre>")
        return _stub(f"Voz TTS → {value}", next_actions=["/set voice", "/help settings"])

    if sub == "channel":
        if value not in ("watch", "tg", "home", None):
            return _error("Canal invalido. Opciones: watch, tg, home")
        return _stub(f"Canal → {value}", next_actions=["/set channel", "/help settings"])

    return _error(
        "Ajuste no reconocido. Disponibles: voice, channel",
        next_actions=["/set voice <nombre>", "/set channel <canal>"],
    )


# ── Home / Domotica ──────────────────────────────────────────────────────────


async def _handle_home(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    """Route smart home commands to the HA executor."""
    if not ctx.ha_executor:
        return _error(
            "Home Assistant no configurado. Anade HA_URL y HA_TOKEN en .env",
            next_actions=["/ping"],
        )

    sub = cmd.subcommand
    args_text = " ".join(cmd.args) if cmd.args else ""

    if sub == "say":
        if not args_text:
            return _error('Falta el texto: /home say "mensaje"')
        result = await ctx.ha_executor.execute_domotica(
            action="tts", parameters={"message": args_text},
        )
        return _from_exec(result, ["/home status"])

    if sub == "announce":
        if not args_text:
            return _error('Falta el texto: /home announce "mensaje" --room sala')
        room = cmd.flags.get("room")
        result = await ctx.ha_executor.execute_domotica(
            action="tts", target=room, parameters={"message": args_text},
        )
        return _from_exec(result, ["/home status"])

    if sub == "volume":
        if not args_text:
            return _error("Falta el volumen: /home volume <0-100>")
        try:
            vol = int(args_text)
        except ValueError:
            return _error("El volumen debe ser un numero entre 0 y 100.")
        result = await ctx.ha_executor.execute_domotica(
            action="volume", parameters={"volume": vol},
        )
        return _from_exec(result, ["/home status", "/home volume"])

    if sub == "lights":
        state = (cmd.args[0].lower() if cmd.args else "on")
        room = cmd.flags.get("room")
        action = "turn_on" if state == "on" else "turn_off"
        result = await ctx.ha_executor.execute_domotica(
            action=action, target=room or "lights",
        )
        opposite = "off" if state == "on" else "on"
        return _from_exec(result, ["/home status", f"/home lights {opposite}"])

    if sub == "scene":
        if not args_text:
            return _error("Falta la escena: /home scene <nombre>")
        result = await ctx.ha_executor.execute_domotica(
            action="scene", scene=args_text,
        )
        return _from_exec(result, ["/home status"])

    if sub == "status":
        result = await ctx.ha_executor.get_all_states()
        return _from_exec(result, ["/home lights on", "/home scene"])

    if not sub:
        return _error(
            "Falta el subcomando: /home <say|announce|volume|lights|scene|status>",
            next_actions=["/help home"],
        )

    return _error(
        f"Subcomando no reconocido: /home {sub}\n"
        "Disponibles: say, announce, volume, lights, scene, status",
        next_actions=["/help home"],
    )


def _from_exec(exec_result: Any, next_actions: list[str]) -> CommandResult:
    """Convert an ExecutionResult from an executor into a CommandResult."""
    return CommandResult(
        run_id=_run_id(),
        status="done" if exec_result.success else "error",
        progress=1.0,
        summary=exec_result.output,
        next_actions=next_actions,
    )


# ── Watch (stubs) ────────────────────────────────────────────────────────────


async def _handle_watch(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    sub = cmd.subcommand
    if sub == "note":
        text = " ".join(cmd.args)
        if not text:
            return _error('Falta el texto: /watch note "nota"')
        return _stub(f"Watch note: {text}", next_actions=["/watch alert"])
    if sub == "alert":
        text = " ".join(cmd.args)
        haptic = cmd.flags.get("haptic", "subtle")
        return _stub(f"Watch alert ({haptic}): {text}", next_actions=["/watch note"])
    if sub == "run":
        text = " ".join(cmd.args)
        if not text:
            return _error('Falta el comando: /watch run "comando"')
        return _stub(f"Watch run: {text}", next_actions=["/watch note"])
    return _error(
        "Subcomando no reconocido. Disponibles: note, alert, run",
        next_actions=["/help watch"],
    )


# ── Scrape (stubs) ───────────────────────────────────────────────────────────


async def _handle_scrape(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    sub = cmd.subcommand
    location = " ".join(cmd.args) if cmd.args else None
    if sub == "centers":
        if not location:
            return _error("Falta la ubicacion: /scrape centers <ubicacion>")
        filters_ = cmd.flags.get("filters", "")
        limit = cmd.flags.get("limit", "20")
        return _stub(
            f"Scrape centros en {location} (filtros={filters_}, limite={limit})",
            next_actions=["/scrape trips"],
        )
    if sub == "trips":
        if not location:
            return _error("Falta la ubicacion: /scrape trips <ubicacion>")
        dates = cmd.flags.get("dates", "")
        budget = cmd.flags.get("budget", "")
        return _stub(
            f"Scrape viajes a {location} (fechas={dates}, presupuesto={budget})",
            next_actions=["/scrape centers"],
        )
    return _error(
        "Subcomando no reconocido. Disponibles: centers, trips",
        next_actions=["/help scrape"],
    )


# ── Data (stubs) ─────────────────────────────────────────────────────────────


async def _handle_verify(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    url = cmd.args[0] if cmd.args else None
    if not url:
        return _error("Falta la URL: /verify <url>")
    return _stub(f"Verificar: {url}", next_actions=["/enrich", "/onboard"])


async def _handle_enrich(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    center_id = cmd.args[0] if cmd.args else None
    if not center_id:
        return _error("Falta el center_id: /enrich <center_id>")
    return _stub(f"Enriquecer centro: {center_id}", next_actions=["/verify", "/onboard"])


async def _handle_onboard(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    sub = cmd.subcommand
    if sub != "center":
        return _error("Uso: /onboard center <url|nombre>", next_actions=["/help data"])
    target = " ".join(cmd.args) if cmd.args else None
    if not target:
        return _error("Falta la URL o nombre: /onboard center <url|nombre>")
    return _stub(f"Onboarding: {target}", next_actions=["/verify", "/enrich"])


# ── Content (stubs) ──────────────────────────────────────────────────────────


async def _handle_colorfix(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    text = " ".join(cmd.args) if cmd.args else None
    if not text:
        return _error("Falta el texto o URL: /colorfix <url|texto>")
    lang = cmd.flags.get("lang", "es")
    tone = cmd.flags.get("tone", "neutral")
    platform = cmd.flags.get("platform", "")
    return _stub(
        f"Colorfix: {text[:60]}... (lang={lang}, tone={tone}, platform={platform})",
        next_actions=["/adapt"],
    )


async def _handle_adapt(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    content_id = cmd.args[0] if cmd.args else None
    if not content_id:
        return _error("Falta el content_id: /adapt <content_id> --to <formatos>")
    formats = cmd.flags.get("to", "")
    if not formats:
        return _error("Falta --to: /adapt <content_id> --to \"reel,carousel,landing\"")
    return _stub(
        f"Adaptar {content_id} a: {formats}",
        next_actions=["/colorfix"],
    )


# ── Music (stubs) ────────────────────────────────────────────────────────────


async def _handle_music(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
    sub = cmd.subcommand
    file_arg = " ".join(cmd.args) if cmd.args else None

    if sub == "analyze":
        if not file_arg:
            return _error("Falta el archivo: /music analyze <archivo>")
        return _stub(f"Analisis musical: {file_arg}", next_actions=["/music coach", "/music drill"])

    if sub == "coach":
        if not file_arg:
            return _error("Falta el archivo: /music coach <archivo>")
        level = cmd.flags.get("level", "beginner")
        goal = cmd.flags.get("goal", "general")
        return _stub(
            f"Coaching ({level}, objetivo={goal}): {file_arg}",
            next_actions=["/music analyze", "/music drill"],
        )

    if sub == "drill":
        if not file_arg:
            return _error("Falta la cancion: /music drill <cancion>")
        bars = cmd.flags.get("bars", "all")
        return _stub(
            f"Drill (compases={bars}): {file_arg}",
            next_actions=["/music analyze", "/music coach"],
        )

    return _error(
        "Subcomando no reconocido. Disponibles: analyze, coach, drill",
        next_actions=["/help music"],
    )


# ── Handler table ────────────────────────────────────────────────────────────

_BUILTIN_HANDLERS: dict[str, HandlerFn] = {
    # System
    "help": _handle_help,
    "ping": _handle_ping,
    "run": _handle_run,
    "status": _handle_status,
    "cancel": _handle_cancel,
    "retry": _handle_retry,
    "summarize": _handle_summarize,
    # Memory
    "remember": _handle_remember,
    "forget": _handle_forget,
    # Settings
    "set": _handle_set,
    # Home
    "home": _handle_home,
    # Watch
    "watch": _handle_watch,
    # Scrape
    "scrape": _handle_scrape,
    # Data
    "verify": _handle_verify,
    "enrich": _handle_enrich,
    "onboard": _handle_onboard,
    # Content
    "colorfix": _handle_colorfix,
    "adapt": _handle_adapt,
    # Music
    "music": _handle_music,
}


# ── Main parser class ───────────────────────────────────────────────────────


class SlashCommandParser:
    """Parse and execute slash commands.

    Usage::

        parser = SlashCommandParser(ctx)
        result = await parser.execute("/home lights on --room salon")
        if result is None:
            # Not a slash command → fall through to NLU
            ...

    Extensibility – register external handlers at class level::

        @SlashCommandParser.register("mycommand")
        async def handle_mycommand(cmd, ctx):
            return CommandResult(...)
    """

    # External handlers registered via the @register decorator
    _external_handlers: ClassVar[dict[str, HandlerFn]] = {}

    def __init__(self, ctx: CommandContext) -> None:
        self._ctx = ctx
        self._registry = ctx.registry
        self._subcommand_names = self._registry.names_with_subcommands()

        # Merge built-in + externally registered handlers
        self._handlers: dict[str, HandlerFn] = {}
        self._handlers.update(_BUILTIN_HANDLERS)
        self._handlers.update(self._external_handlers)

    # ── Class-level decorator for external registration ──────────────────

    @classmethod
    def register(cls, name: str) -> Callable[[HandlerFn], HandlerFn]:
        """Decorator to register an external command handler.

        Example::

            @SlashCommandParser.register("deploy")
            async def handle_deploy(cmd: ParsedCommand, ctx: CommandContext) -> CommandResult:
                ...
        """

        def decorator(func: HandlerFn) -> HandlerFn:
            cls._external_handlers[name] = func
            return func

        return decorator

    # ── Parsing ──────────────────────────────────────────────────────────

    def parse(self, message: str) -> ParsedCommand | None:
        """Parse a slash command string into structured parts.

        Returns None if the message is not a slash command.
        Supports:
        - /command arg1 arg2
        - /command "multi word arg"
        - /command --flag value
        - /command subcommand args --flag value
        """
        if not message or not message.startswith("/"):
            return None

        tokens = _tokenize(message)
        if not tokens:
            return None

        # Extract command name (strip leading / and optional @botname)
        raw_cmd = tokens[0].lstrip("/").lower()
        if not raw_cmd:
            return None
        if "@" in raw_cmd:
            raw_cmd = raw_cmd.split("@", 1)[0]

        remaining = tokens[1:]

        # Detect subcommand for commands that expect one
        subcommand: str | None = None
        if raw_cmd in self._subcommand_names and remaining:
            subcommand = remaining[0].lower()
            remaining = remaining[1:]

        # Separate flags (--key value) from positional args
        args: list[str] = []
        flags: dict[str, str] = {}
        i = 0
        while i < len(remaining):
            token = remaining[i]
            if token.startswith("--") and len(token) > 2:
                flag_name = token[2:]
                # Next token is the value, unless it's another flag or end
                if i + 1 < len(remaining) and not remaining[i + 1].startswith("--"):
                    flags[flag_name] = remaining[i + 1]
                    i += 2
                else:
                    flags[flag_name] = "true"
                    i += 1
            else:
                args.append(token)
                i += 1

        return ParsedCommand(
            name=raw_cmd,
            subcommand=subcommand,
            args=args,
            flags=flags,
            raw=message,
        )

    # ── Execution ────────────────────────────────────────────────────────

    async def execute(self, message: str) -> CommandResult | None:
        """Parse and execute a slash command.

        Returns:
            CommandResult if the command was handled.
            None if the message is not a slash command (caller should use NLU).
        """
        cmd = self.parse(message)
        if cmd is None:
            return None

        handler = self._handlers.get(cmd.name)
        if handler is None:
            # Command starts with / but isn't registered
            return _error(
                f"Comando desconocido: /{cmd.name}\n"
                "Usa /help para ver los comandos disponibles.",
            )

        try:
            return await handler(cmd, self._ctx)
        except Exception as e:
            logger.exception("Slash command handler failed: /%s", cmd.name)
            return _error(f"Error ejecutando /{cmd.name}: {e}")

    # ── Quick check ──────────────────────────────────────────────────────

    @staticmethod
    def is_slash_command(message: str) -> bool:
        """Fast check – is this message a slash command?"""
        return bool(message and message.startswith("/"))
