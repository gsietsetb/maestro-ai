"""Intent parser – uses Gemini API for fast, structured intent classification.

Switched from Claude to Gemini for:
- Native structured JSON output (responseMimeType + responseSchema)
- Lower latency with thinkingLevel: minimal
- Consistency with the rest of the stack (plinng-api, divenamic-ai)
- Native multimodal support (audio/image in same call)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.providers.gemini import GeminiProvider

logger = logging.getLogger(__name__)

VALID_ACTIONS = {
    "code_change",     # AI makes code changes (Cursor Background Agent / Claude Code)
    "operation",       # Run a predefined command (test, lint, build, dev)
    "deploy",          # Deploy a project (destructive – needs confirmation)
    "git",             # Git operations (status, branch, pull, push, log)
    "query",           # Read-only question about a specific project
    "conversation",    # Freeform question about anything (code, architecture, debugging, etc.)
    "plan",            # Ask the AI to plan a task before executing
    "git_force_push",  # Destructive git operation
    "delete_branch",   # Destructive git operation
    "domotica",        # Smart home: control devices, scenes, automation, status
}

SYSTEM_PROMPT = """\
You are an intent parser for a developer orchestration system. Your job is to \
classify natural-language messages from a developer into structured JSON actions.

Available projects: {projects}

CRITICAL: This user is a BUILDER. When they describe an idea, feature, or task, \
they want it EXECUTED, not discussed. Default to ACTION over conversation.

Rules:
- Match project names by name OR alias. "la API" → "plinng-api-MARKETIQ", "expo" → "plinng-expo".
- "tests" / "test" → action "operation", command "test".
- "deploy" / "desplegar" → action "deploy".
- Question about a specific project's code/state → "query" (with project set).
- Make code changes, add features, fix bugs, refactor → "code_change".
- Force push or delete branch → use destructive variants.

ACTION BIAS rules (VERY IMPORTANT):
- If the user describes something to BUILD, CREATE, ADD, FIX, IMPLEMENT → "code_change" (NOT conversation).
- "crea una landing page" → "code_change" (with the most relevant project or null).
- "investiga X" / "averigua Y" → "plan" (this triggers real research, not just chat).
- "haz X" / "implementa Y" / "anade Z" → "code_change".
- "mejora el chatbot" → "code_change" (with the relevant project).
- ONLY use "conversation" when the user is EXPLICITLY asking a question expecting a text answer.
- ONLY use "conversation" for greetings, opinions, debugging questions where no code change is needed.
- When in doubt between "code_change" and "conversation", choose "code_change".
- When in doubt between "plan" and "conversation", choose "plan".

DOMOTICA / SMART HOME rules:
- "enciende/apaga/pon las luces" → action "domotica", ha_action "turn_on"/"turn_off".
- "modo pelicula" / "buenas noches" / activate a scene → "domotica", ha_action "scene".
- "estado de la casa" / "que luces hay" → "domotica", ha_action "status".
- "pon musica" / "sube el volumen" → "domotica", ha_action "play"/"volume".
- "di por el altavoz..." / TTS → "domotica", ha_action "tts".
- "automatizaciones" / "lanza la automatizacion X" → "domotica", ha_action "list_automations"/"trigger_automation".
- Keywords: luces, luz, encender, apagar, salon, cocina, habitacion, TV, altavoz, musica, volumen, escena, modo, casa, temperatura.

The user speaks Spanish, English, or Spanglish. Understand all three.
"""

# Gemini responseSchema for structured output (guarantees valid JSON shape)
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": sorted(VALID_ACTIONS),
            "description": "The classified action type",
        },
        "project": {
            "type": "string",
            "nullable": True,
            "description": "Target project name (null if ambiguous or global)",
        },
        "prompt": {
            "type": "string",
            "nullable": True,
            "description": "Detailed instruction for AI agent (code_change/plan)",
        },
        "command": {
            "type": "string",
            "nullable": True,
            "description": "Predefined command key: dev, build, test, lint, deploy",
        },
        "git_command": {
            "type": "string",
            "nullable": True,
            "description": "Git sub-command: status, branch, pull, push, log, checkout, diff",
        },
        "query_text": {
            "type": "string",
            "nullable": True,
            "description": "Question text for query actions",
        },
        "branch": {
            "type": "string",
            "nullable": True,
            "description": "Target branch name if mentioned",
        },
        "ha_action": {
            "type": "string",
            "nullable": True,
            "description": "Home Assistant action: turn_on, turn_off, toggle, scene, status, play, pause, volume, tts, list_scenes, list_automations, trigger_automation",
        },
        "ha_target": {
            "type": "string",
            "nullable": True,
            "description": "Target device or area: 'luces del salon', 'TV', 'altavoz cocina', entity_id, or friendly name",
        },
        "ha_scene": {
            "type": "string",
            "nullable": True,
            "description": "Scene name to activate: 'movie_mode', 'good_morning', 'buenas_noches', etc.",
        },
        "ha_params": {
            "type": "object",
            "nullable": True,
            "description": "Extra params: brightness (0-100), color (name), volume (0-100), message (for TTS)",
            "properties": {
                "brightness": {"type": "integer", "nullable": True},
                "color": {"type": "string", "nullable": True},
                "volume": {"type": "integer", "nullable": True},
                "message": {"type": "string", "nullable": True},
            },
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the classification (0-1)",
        },
    },
    "required": ["action", "confidence"],
}


@dataclass
class ParsedIntent:
    """Structured output from the intent parser."""

    action: str
    project: Optional[str] = None
    prompt: Optional[str] = None
    command: Optional[str] = None
    git_command: Optional[str] = None
    query_text: Optional[str] = None
    branch: Optional[str] = None
    # Domotica fields
    ha_action: Optional[str] = None
    ha_target: Optional[str] = None
    ha_scene: Optional[str] = None
    ha_params: Optional[dict] = None
    confidence: float = 0.0
    raw_message: str = ""
    image_data: Optional[bytes] = field(default=None, repr=False)


class ConversationHistory:
    """Per-user conversation history for contextual intent parsing."""

    def __init__(self, max_messages: int = 20):
        self._max = max_messages
        self._histories: dict[int, list[dict]] = {}  # user_id -> messages

    def add(self, user_id: int, role: str, content: str) -> None:
        if user_id not in self._histories:
            self._histories[user_id] = []
        self._histories[user_id].append({"role": role, "content": content})
        if len(self._histories[user_id]) > self._max:
            self._histories[user_id] = self._histories[user_id][-self._max :]

    def get(self, user_id: int) -> list[dict]:
        return self._histories.get(user_id, [])

    def clear(self, user_id: int) -> None:
        self._histories.pop(user_id, None)


class IntentParser:
    """Parse natural language into structured intents using Gemini.

    Uses Gemini's native structured JSON output with responseSchema
    for guaranteed valid JSON + thinkingLevel: minimal for low latency.
    """

    def __init__(self, gemini: GeminiProvider):
        self._gemini = gemini
        self.history = ConversationHistory()

    async def parse(
        self,
        message: str,
        project_names: list[str],
        user_id: int | None = None,
    ) -> ParsedIntent:
        """Parse a user message into a structured intent.

        Uses conversation history when user_id is provided for contextual parsing
        (e.g. "now run the tests" after "fix the bug in plinng-web" will infer the project).
        """
        system = SYSTEM_PROMPT.format(projects=", ".join(project_names))

        # Build prompt with conversation context
        if user_id is not None:
            history = self.history.get(user_id)
            if history:
                context_text = "\n".join(
                    f"{'User' if m['role'] == 'user' else 'System'}: {m['content']}"
                    for m in history[-6:]
                )
                prompt = (
                    f"Recent conversation context:\n{context_text}\n\n"
                    f"New message to classify:\n{message}"
                )
            else:
                prompt = message
            self.history.add(user_id, "user", message)
        else:
            prompt = message

        # Call Gemini with structured JSON output + responseSchema
        data = await self._gemini.generate_json(
            prompt=prompt,
            system_prompt=system,
            temperature=0.2,
            max_tokens=512,
            response_schema=INTENT_SCHEMA,
        )

        if not data or not isinstance(data, dict):
            logger.error("Gemini returned invalid intent data: %s", data)
            raise ValueError("El parser de intenciones no devolvio datos validos.")

        action = data.get("action", "query")
        if action not in VALID_ACTIONS:
            action = "query"

        intent = ParsedIntent(
            action=action,
            project=data.get("project"),
            prompt=data.get("prompt"),
            command=data.get("command"),
            git_command=data.get("git_command"),
            query_text=data.get("query_text"),
            branch=data.get("branch"),
            ha_action=data.get("ha_action"),
            ha_target=data.get("ha_target"),
            ha_scene=data.get("ha_scene"),
            ha_params=data.get("ha_params"),
            confidence=float(data.get("confidence", 0.0)),
            raw_message=message,
        )

        if user_id is not None:
            self.history.add(
                user_id,
                "assistant",
                f"[action={intent.action}, project={intent.project}]",
            )

        return intent
