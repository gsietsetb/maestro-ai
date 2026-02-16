"""Command registry – metadata and discovery for all slash commands.

Provides a central catalog of every slash command with name, description,
usage, examples, and category.  Used by /help to show available commands
and by the parser for validation and autocomplete suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Category display names ───────────────────────────────────────────────────

CATEGORY_LABELS: dict[str, str] = {
    "system": "Sistema",
    "memory": "Memoria",
    "settings": "Ajustes",
    "home": "Casa / Domotica",
    "watch": "Apple Watch",
    "scrape": "Scraping",
    "data": "Datos",
    "content": "Contenido",
    "music": "Musica",
}

CATEGORY_ICONS: dict[str, str] = {
    "system": "\U0001f527",   # wrench
    "memory": "\U0001f9e0",   # brain
    "settings": "\u2699\ufe0f",  # gear
    "home": "\U0001f3e0",     # house
    "watch": "\u231a",        # watch
    "scrape": "\U0001f578\ufe0f",  # spider web
    "data": "\U0001f4ca",     # bar chart
    "content": "\U0001f3a8",  # palette
    "music": "\U0001f3b5",    # musical note
}


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class CommandMeta:
    """Metadata for a single slash command."""

    name: str
    description: str
    usage: str
    examples: list[str] = field(default_factory=list)
    category: str = "system"
    has_subcommands: bool = False
    aliases: list[str] = field(default_factory=list)


# ── Registry class ───────────────────────────────────────────────────────────


class CommandRegistry:
    """Registry of all available slash commands with metadata.

    Supports registration, lookup, category grouping, and help formatting.
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandMeta] = {}

    def register(self, meta: CommandMeta) -> None:
        """Register a command (and its aliases)."""
        self._commands[meta.name] = meta
        for alias in meta.aliases:
            self._commands[alias] = meta

    def get(self, name: str) -> CommandMeta | None:
        """Look up a command by name or alias."""
        return self._commands.get(name)

    def all_commands(self) -> list[CommandMeta]:
        """Return all unique commands sorted by category then name."""
        seen: set[str] = set()
        result: list[CommandMeta] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return sorted(result, key=lambda c: (c.category, c.name))

    def by_category(self) -> dict[str, list[CommandMeta]]:
        """Group commands by category."""
        cats: dict[str, list[CommandMeta]] = {}
        for cmd in self.all_commands():
            cats.setdefault(cmd.category, []).append(cmd)
        return cats

    def names_with_subcommands(self) -> set[str]:
        """Return command names that expect a subcommand."""
        return {c.name for c in self._commands.values() if c.has_subcommands}

    def command_names(self) -> list[str]:
        """Return all registered command names (no aliases)."""
        return [c.name for c in self.all_commands()]

    def format_help(self, category: str | None = None) -> str:
        """Format a human-readable help text, optionally filtered by category."""
        groups = self.by_category()

        if category:
            category = category.lower()
            commands = groups.get(category)
            if not commands:
                available = ", ".join(groups.keys())
                return f"Categoria '{category}' no encontrada. Disponibles: {available}"
            icon = CATEGORY_ICONS.get(category, "")
            label = CATEGORY_LABELS.get(category, category.title())
            lines = [f"{icon} {label}\n"]
            for cmd in commands:
                lines.append(f"  /{cmd.usage}  —  {cmd.description}")
                if cmd.examples:
                    for ex in cmd.examples[:2]:
                        lines.append(f"      ej: {ex}")
            return "\n".join(lines)

        # Full help – all categories
        lines = ["Comandos disponibles:\n"]
        for cat, commands in groups.items():
            icon = CATEGORY_ICONS.get(cat, "")
            label = CATEGORY_LABELS.get(cat, cat.title())
            lines.append(f"\n{icon} {label}")
            for cmd in commands:
                lines.append(f"  /{cmd.usage}  —  {cmd.description}")
        lines.append("\nUsa /help <categoria> para mas detalle.")
        return "\n".join(lines)


# ── Default registry with all built-in commands ─────────────────────────────


def build_default_registry() -> CommandRegistry:
    """Build the registry pre-loaded with every built-in command."""
    r = CommandRegistry()

    # ── System ────────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="help",
        description="Lista todos los comandos disponibles",
        usage="help [categoria]",
        examples=["/help", "/help home"],
        category="system",
    ))
    r.register(CommandMeta(
        name="ping",
        description="Chequeo de salud del sistema",
        usage="ping",
        examples=["/ping"],
        category="system",
    ))
    r.register(CommandMeta(
        name="run",
        description="Ejecutar comando generico via NLU",
        usage="run <texto>",
        examples=["/run deploy plinng-web", "/run tests en la api"],
        category="system",
    ))
    r.register(CommandMeta(
        name="status",
        description="Ver progreso de una tarea",
        usage="status <run_id>",
        examples=["/status abc123"],
        category="system",
    ))
    r.register(CommandMeta(
        name="cancel",
        description="Cancelar una tarea en ejecucion",
        usage="cancel <run_id>",
        examples=["/cancel abc123"],
        category="system",
    ))
    r.register(CommandMeta(
        name="retry",
        description="Reintentar una tarea fallida",
        usage="retry <run_id>",
        examples=["/retry abc123"],
        category="system",
    ))
    r.register(CommandMeta(
        name="summarize",
        description="Resumir resultado o texto",
        usage="summarize <run_id|texto>",
        examples=["/summarize abc123", '/summarize "texto largo..."'],
        category="system",
    ))

    # ── Memory ────────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="remember",
        description="Guardar en memoria",
        usage="remember <texto>",
        examples=['/remember "La API usa JWT auth"'],
        category="memory",
    ))
    r.register(CommandMeta(
        name="forget",
        description="Borrar memoria sobre un tema",
        usage="forget <tema>",
        examples=['/forget "JWT auth"'],
        category="memory",
    ))

    # ── Settings ──────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="set",
        description="Cambiar ajustes",
        usage="set <clave> <valor>",
        examples=["/set voice alloy", "/set channel tg"],
        category="settings",
        has_subcommands=True,
    ))

    # ── Home / Domotica ───────────────────────────────────────────────────
    r.register(CommandMeta(
        name="home",
        description="Control de casa inteligente",
        usage="home <accion> [args]",
        examples=[
            '/home say "la cena esta lista"',
            '/home announce "buenos dias" --room dormitorio',
            "/home volume 50",
            "/home lights on --room salon",
            "/home scene pelicula",
            "/home status",
        ],
        category="home",
        has_subcommands=True,
    ))

    # ── Watch ─────────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="watch",
        description="Integracion Apple Watch",
        usage="watch <accion> [args]",
        examples=[
            '/watch note "idea para el proyecto"',
            '/watch alert "deploy listo" --haptic strong',
            '/watch run "git status"',
        ],
        category="watch",
        has_subcommands=True,
    ))

    # ── Scrape ────────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="scrape",
        description="Herramientas de scraping web",
        usage="scrape <tipo> <ubicacion> [flags]",
        examples=[
            '/scrape centers "Bali" --filters "freediving, scuba" --limit 30',
            '/scrape trips "Thailand" --dates 2026-04 --budget 1500',
        ],
        category="scrape",
        has_subcommands=True,
    ))

    # ── Data ──────────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="verify",
        description="Extraer hechos y limpiar datos",
        usage="verify <url>",
        examples=["/verify https://example.com/center"],
        category="data",
    ))
    r.register(CommandMeta(
        name="enrich",
        description="Completar perfil de centro",
        usage="enrich <center_id>",
        examples=["/enrich center_123"],
        category="data",
    ))
    r.register(CommandMeta(
        name="onboard",
        description="Wizard de onboarding",
        usage="onboard center <url|nombre>",
        examples=['/onboard center "Blue Corner Diving"'],
        category="data",
        has_subcommands=True,
    ))

    # ── Content ───────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="colorfix",
        description="Corregir / traducir / adaptar contenido",
        usage='colorfix <url|texto> [flags]',
        examples=['/colorfix "texto" --lang es --tone casual --platform ig'],
        category="content",
    ))
    r.register(CommandMeta(
        name="adapt",
        description="Adaptar contenido a formatos",
        usage="adapt <content_id> --to <formatos>",
        examples=['/adapt post_123 --to "reel,carousel,landing"'],
        category="content",
    ))

    # ── Music ─────────────────────────────────────────────────────────────
    r.register(CommandMeta(
        name="music",
        description="Analisis musical y coaching",
        usage="music <accion> <archivo> [flags]",
        examples=[
            "/music analyze song.mp3",
            "/music coach song.mp3 --level beginner --goal pitch",
            "/music drill song.mp3 --bars 12-24",
        ],
        category="music",
        has_subcommands=True,
    ))

    return r
