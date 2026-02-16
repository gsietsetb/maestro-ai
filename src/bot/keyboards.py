"""Inline keyboards for Telegram confirmations and menus."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def confirm_keyboard(action_id: str) -> InlineKeyboardMarkup:
    """Yes / No confirmation for destructive operations."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Si, ejecutar", callback_data=f"confirm:{action_id}"),
                InlineKeyboardButton("Cancelar", callback_data=f"cancel:{action_id}"),
            ]
        ]
    )


def project_picker(projects: list[str]) -> InlineKeyboardMarkup:
    """Let the user pick a project from a list."""
    buttons = [[InlineKeyboardButton(p, callback_data=f"project:{p}")] for p in projects]
    buttons.append([InlineKeyboardButton("Cancelar", callback_data="cancel:picker")])
    return InlineKeyboardMarkup(buttons)


def action_menu() -> InlineKeyboardMarkup:
    """Quick-action menu with all features."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Proyectos", callback_data="menu:projects"),
                InlineKeyboardButton("Tareas", callback_data="menu:tasks"),
            ],
            [
                InlineKeyboardButton("Casa", callback_data="menu:casa"),
                InlineKeyboardButton("Mesh", callback_data="menu:mesh"),
            ],
            [
                InlineKeyboardButton("Ayuda", callback_data="menu:help"),
            ],
        ]
    )


def detail_button(task_id: str) -> InlineKeyboardMarkup:
    """Button to expand full output of a completed task."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Ver detalle", callback_data=f"detail:{task_id}")]]
    )


def domotica_menu() -> InlineKeyboardMarkup:
    """Quick domotica actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Estado casa", callback_data="casa:status"),
                InlineKeyboardButton("Escenas", callback_data="casa:scenes"),
            ],
            [
                InlineKeyboardButton("Luces ON", callback_data="casa:lights_on"),
                InlineKeyboardButton("Luces OFF", callback_data="casa:lights_off"),
            ],
            [
                InlineKeyboardButton("Automatizaciones", callback_data="casa:automations"),
            ],
        ]
    )
