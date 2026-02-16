"""Home Assistant executor – controls your smart home from Telegram/WhatsApp.

Communicates with Home Assistant's REST API to:
- Toggle devices (lights, switches, plugs, speakers, TV)
- Activate scenes (movie mode, good morning, good night, etc.)
- Run automations
- Get device/sensor states
- Call any HA service

Supports Google Home + Alexa + any HA-integrated device.

HA REST API docs: https://developers.home-assistant.io/docs/api/rest/
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)


class HomeAssistantExecutor:
    """Home Assistant REST API client for smart home control."""

    def __init__(self, url: str, token: str, timeout: float = 15.0):
        """
        Args:
            url: HA base URL, e.g. http://192.168.1.100:8123
            token: Long-lived access token from HA
        """
        self._url = url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{self._url}/api",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── Health check ──────────────────────────────────────────────────────

    async def check_connection(self) -> bool:
        """Verify connection to Home Assistant."""
        try:
            resp = await self._client.get("/")
            return resp.status_code == 200
        except Exception as e:
            logger.warning("HA connection check failed: %s", e)
            return False

    # ── Device control ────────────────────────────────────────────────────

    async def turn_on(self, entity_id: str, **kwargs) -> ExecutionResult:
        """Turn on a device (light, switch, media_player, etc.)."""
        domain = entity_id.split(".")[0]
        return await self._call_service(
            domain=domain,
            service="turn_on",
            entity_id=entity_id,
            **kwargs,
        )

    async def turn_off(self, entity_id: str) -> ExecutionResult:
        """Turn off a device."""
        domain = entity_id.split(".")[0]
        return await self._call_service(
            domain=domain,
            service="turn_off",
            entity_id=entity_id,
        )

    async def toggle(self, entity_id: str) -> ExecutionResult:
        """Toggle a device on/off."""
        domain = entity_id.split(".")[0]
        return await self._call_service(
            domain=domain,
            service="toggle",
            entity_id=entity_id,
        )

    async def set_light(
        self,
        entity_id: str,
        brightness: int | None = None,
        color_name: str | None = None,
        rgb_color: tuple[int, int, int] | None = None,
        color_temp: int | None = None,
    ) -> ExecutionResult:
        """Control a light with specific parameters."""
        service_data: dict[str, Any] = {}
        if brightness is not None:
            service_data["brightness_pct"] = max(0, min(100, brightness))
        if color_name:
            service_data["color_name"] = color_name
        if rgb_color:
            service_data["rgb_color"] = list(rgb_color)
        if color_temp:
            service_data["color_temp"] = color_temp

        return await self._call_service(
            domain="light",
            service="turn_on",
            entity_id=entity_id,
            **service_data,
        )

    # ── Scenes ────────────────────────────────────────────────────────────

    async def activate_scene(self, scene_id: str) -> ExecutionResult:
        """Activate a scene (e.g. scene.movie_mode, scene.good_morning)."""
        if not scene_id.startswith("scene."):
            scene_id = f"scene.{scene_id}"
        return await self._call_service(
            domain="scene",
            service="turn_on",
            entity_id=scene_id,
        )

    async def list_scenes(self) -> ExecutionResult:
        """List all available scenes."""
        states = await self._get_states(domain="scene")
        if states is None:
            return ExecutionResult(success=False, output="Error obteniendo escenas de HA.")

        lines = ["Escenas disponibles:\n"]
        for state in states:
            name = state.get("attributes", {}).get("friendly_name", state["entity_id"])
            lines.append(f"  - {name} ({state['entity_id']})")

        return ExecutionResult(success=True, output="\n".join(lines))

    # ── Automations ───────────────────────────────────────────────────────

    async def trigger_automation(self, automation_id: str) -> ExecutionResult:
        """Trigger an automation manually."""
        if not automation_id.startswith("automation."):
            automation_id = f"automation.{automation_id}"
        return await self._call_service(
            domain="automation",
            service="trigger",
            entity_id=automation_id,
        )

    async def list_automations(self) -> ExecutionResult:
        """List all automations and their states."""
        states = await self._get_states(domain="automation")
        if states is None:
            return ExecutionResult(success=False, output="Error obteniendo automatizaciones.")

        lines = ["Automatizaciones:\n"]
        for state in states:
            name = state.get("attributes", {}).get("friendly_name", state["entity_id"])
            status = state.get("state", "?")
            icon = "ON" if status == "on" else "OFF"
            lines.append(f"  [{icon}] {name}")

        return ExecutionResult(success=True, output="\n".join(lines))

    # ── Status / monitoring ───────────────────────────────────────────────

    async def get_state(self, entity_id: str) -> ExecutionResult:
        """Get the current state of an entity."""
        try:
            resp = await self._client.get(f"/states/{entity_id}")
            if resp.status_code == 404:
                return ExecutionResult(
                    success=False,
                    output=f"Entidad '{entity_id}' no encontrada en Home Assistant.",
                )
            resp.raise_for_status()
            data = resp.json()

            name = data.get("attributes", {}).get("friendly_name", entity_id)
            state = data.get("state", "unknown")
            attrs = data.get("attributes", {})

            lines = [f"{name}: {state}"]

            # Add relevant attributes based on domain
            domain = entity_id.split(".")[0]
            if domain == "light" and state == "on":
                if "brightness" in attrs:
                    pct = round(attrs["brightness"] / 255 * 100)
                    lines.append(f"  Brillo: {pct}%")
                if "color_temp" in attrs:
                    lines.append(f"  Temp color: {attrs['color_temp']}")
            elif domain == "climate":
                if "current_temperature" in attrs:
                    lines.append(f"  Temp actual: {attrs['current_temperature']}C")
                if "temperature" in attrs:
                    lines.append(f"  Temp objetivo: {attrs['temperature']}C")
            elif domain == "media_player":
                if "media_title" in attrs:
                    lines.append(f"  Reproduciendo: {attrs['media_title']}")
                if "volume_level" in attrs:
                    vol = round(attrs["volume_level"] * 100)
                    lines.append(f"  Volumen: {vol}%")
            elif domain == "sensor":
                unit = attrs.get("unit_of_measurement", "")
                lines[0] = f"{name}: {state} {unit}"

            return ExecutionResult(success=True, output="\n".join(lines))

        except Exception as e:
            return ExecutionResult(success=False, output=f"Error: {e}")

    async def get_all_states(self) -> ExecutionResult:
        """Get a summary of all devices grouped by type."""
        try:
            resp = await self._client.get("/states")
            resp.raise_for_status()
            states = resp.json()
        except Exception as e:
            return ExecutionResult(success=False, output=f"Error conectando con HA: {e}")

        # Group by domain
        groups: dict[str, list] = {}
        for s in states:
            domain = s["entity_id"].split(".")[0]
            if domain in ("light", "switch", "media_player", "climate", "scene",
                          "automation", "sensor", "binary_sensor", "cover", "fan", "lock"):
                groups.setdefault(domain, []).append(s)

        domain_names = {
            "light": "Luces", "switch": "Interruptores", "media_player": "Reproductores",
            "climate": "Clima", "scene": "Escenas", "automation": "Automatizaciones",
            "sensor": "Sensores", "binary_sensor": "Sensores binarios", "cover": "Persianas",
            "fan": "Ventiladores", "lock": "Cerraduras",
        }

        lines = ["Estado de la casa:\n"]
        for domain, items in sorted(groups.items()):
            label = domain_names.get(domain, domain)
            lines.append(f"\n{label} ({len(items)}):")
            for s in items[:15]:  # Max 15 per domain
                name = s.get("attributes", {}).get("friendly_name", s["entity_id"])
                state = s.get("state", "?")
                lines.append(f"  {name}: {state}")

        return ExecutionResult(
            success=True,
            output="\n".join(lines) if len(lines) > 1 else "No se encontraron dispositivos.",
        )

    # ── Media player (speakers, TV) ──────────────────────────────────────

    async def media_play(self, entity_id: str) -> ExecutionResult:
        return await self._call_service("media_player", "media_play", entity_id)

    async def media_pause(self, entity_id: str) -> ExecutionResult:
        return await self._call_service("media_player", "media_pause", entity_id)

    async def media_next(self, entity_id: str) -> ExecutionResult:
        return await self._call_service("media_player", "media_next_track", entity_id)

    async def set_volume(self, entity_id: str, volume: int) -> ExecutionResult:
        return await self._call_service(
            "media_player", "volume_set", entity_id,
            volume_level=max(0.0, min(1.0, volume / 100)),
        )

    async def play_tts(self, entity_id: str, message: str) -> ExecutionResult:
        """Text-to-speech on a speaker (Google Home, Alexa, etc.)."""
        return await self._call_service(
            "tts", "speak",
            entity_id=entity_id,
            message=message,
        )

    # ── Generic service call ──────────────────────────────────────────────

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        **service_data,
    ) -> ExecutionResult:
        """Call any HA service (advanced/raw)."""
        return await self._call_service(domain, service, entity_id, **service_data)

    # ── Smart dispatch (from natural language parsed intent) ──────────────

    async def execute_domotica(
        self,
        action: str,
        target: str | None = None,
        scene: str | None = None,
        parameters: dict | None = None,
    ) -> ExecutionResult:
        """Smart dispatch based on parsed intent fields.

        Args:
            action: "turn_on", "turn_off", "toggle", "scene", "status", "list", etc.
            target: entity_id or friendly name of the device
            scene: scene name to activate
            parameters: additional params (brightness, color, volume, etc.)
        """
        params = parameters or {}

        # ── Scenes ────────────────────────────────────────────────────────
        if action == "scene" and scene:
            return await self.activate_scene(scene)

        if action == "list_scenes":
            return await self.list_scenes()

        if action == "list_automations":
            return await self.list_automations()

        # ── Status ────────────────────────────────────────────────────────
        if action == "status":
            if target:
                entity_id = await self._resolve_entity(target)
                if entity_id:
                    return await self.get_state(entity_id)
                return ExecutionResult(success=False, output=f"Dispositivo '{target}' no encontrado.")
            return await self.get_all_states()

        # ── Trigger automation ────────────────────────────────────────────
        if action == "trigger_automation" and target:
            return await self.trigger_automation(target)

        # ── Device control ────────────────────────────────────────────────
        if not target:
            return ExecutionResult(
                success=False,
                output="Necesito saber que dispositivo quieres controlar. "
                "Ej: 'enciende las luces del salon' o 'apaga la TV'.",
            )

        entity_id = await self._resolve_entity(target)
        if not entity_id:
            return ExecutionResult(
                success=False,
                output=f"Dispositivo '{target}' no encontrado en Home Assistant.",
            )

        if action == "turn_on":
            # Special handling for lights with parameters
            if entity_id.startswith("light.") and params:
                return await self.set_light(
                    entity_id,
                    brightness=params.get("brightness"),
                    color_name=params.get("color"),
                    rgb_color=params.get("rgb_color"),
                )
            return await self.turn_on(entity_id)

        if action == "turn_off":
            return await self.turn_off(entity_id)

        if action == "toggle":
            return await self.toggle(entity_id)

        # Media controls
        if action == "play":
            return await self.media_play(entity_id)
        if action == "pause":
            return await self.media_pause(entity_id)
        if action == "next":
            return await self.media_next(entity_id)
        if action == "volume" and "volume" in params:
            return await self.set_volume(entity_id, params["volume"])
        if action == "tts" and "message" in params:
            return await self.play_tts(entity_id, params["message"])

        return ExecutionResult(
            success=False,
            output=f"Accion '{action}' no reconocida para domotica.",
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | None = None,
        **service_data,
    ) -> ExecutionResult:
        """Call a Home Assistant service."""
        payload: dict[str, Any] = {}
        if entity_id:
            payload["entity_id"] = entity_id
        payload.update(service_data)

        try:
            resp = await self._client.post(
                f"/services/{domain}/{service}",
                json=payload,
            )
            resp.raise_for_status()

            # Build a friendly response
            friendly = entity_id or f"{domain}.{service}"
            action_es = {
                "turn_on": "encendido",
                "turn_off": "apagado",
                "toggle": "alternado",
                "media_play": "reproduciendo",
                "media_pause": "pausado",
                "media_next_track": "siguiente pista",
                "volume_set": "volumen ajustado",
                "trigger": "ejecutado",
            }
            verb = action_es.get(service, service)
            return ExecutionResult(success=True, output=f"{friendly}: {verb}")

        except httpx.HTTPStatusError as e:
            return ExecutionResult(
                success=False,
                output=f"HA error ({e.response.status_code}): {e.response.text[:300]}",
            )
        except Exception as e:
            return ExecutionResult(success=False, output=f"Error comunicando con HA: {e}")

    async def _get_states(self, domain: str | None = None) -> list[dict] | None:
        """Fetch states, optionally filtered by domain."""
        try:
            resp = await self._client.get("/states")
            resp.raise_for_status()
            states = resp.json()
            if domain:
                states = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
            return states
        except Exception as e:
            logger.error("Error fetching HA states: %s", e)
            return None

    async def _resolve_entity(self, target: str) -> str | None:
        """Try to resolve a friendly name or partial entity_id to a full entity_id.

        Supports:
        - Full entity_id: "light.salon" → "light.salon"
        - Friendly name: "Luces del salon" → "light.salon" (by matching attributes)
        - Partial match: "salon" → first entity containing "salon"
        """
        # Already a valid entity_id
        if "." in target and not " " in target:
            return target

        # Search by friendly name or entity_id
        states = await self._get_states()
        if not states:
            return None

        target_lower = target.lower()

        # Exact friendly name match
        for s in states:
            name = s.get("attributes", {}).get("friendly_name", "").lower()
            if name == target_lower:
                return s["entity_id"]

        # Partial match in friendly name
        for s in states:
            name = s.get("attributes", {}).get("friendly_name", "").lower()
            if target_lower in name:
                return s["entity_id"]

        # Partial match in entity_id
        for s in states:
            if target_lower in s["entity_id"].lower():
                return s["entity_id"]

        return None
