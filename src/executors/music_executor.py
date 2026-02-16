"""Music AI executor – calls the music-ai microservice for singing analysis.

Endpoints (music-ai FastAPI at MUSIC_AI_URL):
- POST /analyze  – full analysis (separate + pitch + feedback)
- POST /separate – source separation only
- POST /pitch    – pitch detection only
- GET  /health   – health check
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from src.orchestrator.router import ExecutionResult

logger = logging.getLogger(__name__)


class MusicAIExecutor:
    """Client for the music-ai microservice."""

    def __init__(self, base_url: str, timeout: float = 120.0):
        self._url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._url,
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def check_connection(self) -> bool:
        """Check if the music-ai service is reachable."""
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except Exception as e:
            logger.warning("Music AI health check failed: %s", e)
            return False

    async def analyze(
        self,
        reference_path: str,
        user_path: str,
    ) -> ExecutionResult:
        """Full singing analysis: separate vocals, detect pitch, compare, feedback."""
        try:
            files = {
                "reference": (
                    Path(reference_path).name,
                    open(reference_path, "rb"),
                    "audio/mpeg",
                ),
                "recording": (
                    Path(user_path).name,
                    open(user_path, "rb"),
                    "audio/mpeg",
                ),
            }
            resp = await self._client.post("/analyze", files=files)
            resp.raise_for_status()
            data = resp.json()

            score = data.get("overall_score", "?")
            pitch_acc = data.get("pitch_accuracy", 0)
            tips = data.get("tips", [])
            feedback = data.get("detailed_feedback", "")

            lines = [
                f"Puntuacion: {score}/100",
                f"Precision de tono: {pitch_acc:.0%}",
                "",
                feedback,
            ]
            if tips:
                lines.append("\nConsejos:")
                for tip in tips:
                    lines.append(f"  - {tip}")

            return ExecutionResult(success=True, output="\n".join(lines))

        except httpx.HTTPStatusError as e:
            return ExecutionResult(
                success=False,
                output=f"Music AI error ({e.response.status_code}): {e.response.text[:300]}",
            )
        except FileNotFoundError as e:
            return ExecutionResult(success=False, output=f"Archivo no encontrado: {e}")
        except Exception as e:
            return ExecutionResult(success=False, output=f"Error comunicando con Music AI: {e}")

    async def separate(self, audio_path: str) -> ExecutionResult:
        """Source separation: extract vocals, drums, bass, accompaniment."""
        try:
            files = {
                "file": (
                    Path(audio_path).name,
                    open(audio_path, "rb"),
                    "audio/mpeg",
                ),
            }
            resp = await self._client.post("/separate", files=files)
            resp.raise_for_status()
            data = resp.json()

            stems = data.get("stems", {})
            lines = ["Pistas separadas:"]
            for stem_name, info in stems.items():
                lines.append(f"  - {stem_name}: {info.get('path', '?')}")

            return ExecutionResult(success=True, output="\n".join(lines))

        except Exception as e:
            return ExecutionResult(success=False, output=f"Error en separacion: {e}")

    async def detect_pitch(self, audio_path: str) -> ExecutionResult:
        """Pitch detection on an audio file."""
        try:
            files = {
                "file": (
                    Path(audio_path).name,
                    open(audio_path, "rb"),
                    "audio/mpeg",
                ),
            }
            resp = await self._client.post("/pitch", files=files)
            resp.raise_for_status()
            data = resp.json()

            avg_freq = data.get("average_frequency", 0)
            note_range = data.get("note_range", {})
            n_points = data.get("point_count", 0)

            low = note_range.get("lowest", "?")
            high = note_range.get("highest", "?")

            lines = [
                f"Puntos de pitch detectados: {n_points}",
                f"Frecuencia media: {avg_freq:.1f} Hz",
                f"Rango: {low} → {high}",
            ]
            return ExecutionResult(success=True, output="\n".join(lines))

        except Exception as e:
            return ExecutionResult(success=False, output=f"Error en deteccion de pitch: {e}")

    async def execute_music(
        self,
        action: str,
        file_path: Optional[str] = None,
        reference_path: Optional[str] = None,
        parameters: Optional[dict] = None,
    ) -> ExecutionResult:
        """Smart dispatch for music commands from slash parser.

        Actions: analyze, separate, pitch, coach, drill
        """
        params = parameters or {}

        if action == "analyze" and reference_path and file_path:
            return await self.analyze(reference_path, file_path)

        if action == "separate" and file_path:
            return await self.separate(file_path)

        if action == "pitch" and file_path:
            return await self.detect_pitch(file_path)

        if action == "coach":
            level = params.get("level", "beginner")
            goal = params.get("goal", "pitch")
            if file_path:
                result = await self.analyze(file_path, file_path)
                if result.success:
                    result.output = (
                        f"[Coach mode: {level}, goal: {goal}]\n\n" + result.output
                    )
                return result
            return ExecutionResult(
                success=False,
                output="Se necesita un archivo de audio para el modo coach.",
            )

        if action == "drill":
            bars = params.get("bars", "all")
            return ExecutionResult(
                success=False,
                output=f"Modo drill (compases {bars}): proximamente. "
                "Necesita la pista original + seccion especifica.",
            )

        return ExecutionResult(
            success=False,
            output=f"Accion de musica '{action}' no reconocida. "
            "Usa: analyze, separate, pitch, coach, drill.",
        )
