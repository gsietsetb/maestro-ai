"""Format messages for Telegram (MarkdownV2-safe)."""

from __future__ import annotations

import re


def escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters."""
    special = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(f"([{re.escape(special)}])", r"\\\1", text)


def format_task_launched(project: str, action: str, task_id: str) -> str:
    return (
        f"*Tarea lanzada*\n\n"
        f"Proyecto: `{escape_md(project)}`\n"
        f"Accion: `{escape_md(action)}`\n"
        f"ID: `{escape_md(task_id)}`\n\n"
        f"Te aviso cuando termine\\."
    )


def format_task_result(project: str, action: str, success: bool, output: str) -> str:
    """Full output – used as fallback or when user requests detail."""
    status = "Completado" if success else "Error"
    icon = "+" if success else "!"
    max_output = 3000
    if len(output) > max_output:
        output = output[:max_output] + "\n... (truncado)"
    return (
        f"*{escape_md(status)}* \\[{escape_md(icon)}\\]\n\n"
        f"Proyecto: `{escape_md(project)}`\n"
        f"Accion: `{escape_md(action)}`\n\n"
        f"```\n{escape_md(output)}\n```"
    )


def format_task_summary(
    project: str, action: str, success: bool, summary: str, task_id: str
) -> str:
    """Ultra-short summary (2 lines) – the primary response after task completion."""
    icon = "OK" if success else "FALLO"
    # Truncate summary to avoid Telegram message limits
    if len(summary) > 500:
        summary = summary[:500] + "..."
    return (
        f"*\\[{escape_md(icon)}\\]* `{escape_md(project)}` \\> `{escape_md(action)}`\n\n"
        f"{escape_md(summary)}"
    )


def format_project_list(projects: dict) -> str:
    lines = ["*Proyectos registrados*\n"]
    for name, info in projects.items():
        stack = info.get("stack", "?")
        aliases = ", ".join(info.get("aliases", []))
        lines.append(f"  `{escape_md(name)}` \\({escape_md(stack)}\\) — {escape_md(aliases)}")
    return "\n".join(lines)


def format_help() -> str:
    return (
        "*Cursor Orchestrator*\n\n"
        "Enviale mensajes en lenguaje natural\\. Ejemplos:\n\n"
        "*Codigo:*\n"
        "  _Agrega dark mode a plinng\\-web_\n"
        "  _Fix el bug de login en la API_\n"
        "  _Refactoriza el checkout de expo_\n\n"
        "*Preguntas \\(cualquier cosa\\):*\n"
        "  _Como funciona la auth en la API?_\n"
        "  _Que es mejor, Redis o Postgres para esto?_\n"
        "  _Explicame la arquitectura de divenamic_\n\n"
        "*Operaciones:*\n"
        "  _Corre los tests de la API_\n"
        "  _Git status de divenamic_\n"
        "  _Haz deploy de plinng\\-web_\n\n"
        "*Domotica:*\n"
        "  _Enciende las luces del salon_\n"
        "  _Pon modo pelicula_\n"
        "  _Estado de la casa_\n\n"
        "*Multimedia:*\n"
        "  Envia un audio/nota de voz → transcribo y proceso\n"
        "  Envia una foto → la analizo con Gemini Vision\n"
        "  Envia una foto \\+ texto → la proceso como instruccion\n"
        "  Envia un video → lo analizo frame a frame\n"
        "  Envia un documento \\(audio/img/video\\) → lo proceso\n\n"
        "Comandos rapidos:\n"
        "  /projects — listar proyectos\n"
        "  /tasks — tareas activas\n"
        "  /casa — estado domotica\n"
        "  /mesh — agentes conectados\n"
        "  /help — esta ayuda\n"
    )


def format_agent_pr(project: str, pr_url: str) -> str:
    return (
        f"*PR creado*\n\n"
        f"Proyecto: `{escape_md(project)}`\n"
        f"Link: {escape_md(pr_url)}"
    )


def format_error(message: str) -> str:
    return f"*Error*\n\n`{escape_md(message)}`"


def format_mesh_status(mesh_data: dict) -> str:
    """Format agent mesh status for Telegram."""
    connected = mesh_data.get("connected", 0)
    agents = mesh_data.get("agents", [])

    if not agents:
        return (
            "*Agent Mesh*\n\n"
            "No hay agentes conectados\\.\n"
            "Ejecuta el daemon en algun PC:\n"
            "`bash scripts/install_agent\\.sh`"
        )

    lines = [f"*Agent Mesh* — {connected} conectado\\(s\\)\n"]
    for a in agents:
        alive = "ON" if a.get("alive") else "OFF"
        hostname = escape_md(a.get("hostname", "?"))
        load = escape_md(a.get("load", "?"))
        projects = a.get("projects", 0)
        caps = ", ".join(a.get("capabilities", []))
        lines.append(
            f"  \\[{alive}\\] *{hostname}* — {load} tareas — {projects} proyectos"
        )
        if caps:
            lines.append(f"       _{escape_md(caps)}_")

    return "\n".join(lines)


def format_domotica_status(text: str) -> str:
    """Format Home Assistant status for Telegram."""
    return f"*Estado de la casa*\n\n```\n{escape_md(text)}\n```"
