"""Process manager – runs and manages shell processes for the local agent."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class ProcessManager:
    """Manages async subprocess execution with timeouts and cancellation."""

    def __init__(self):
        self._active: dict[str, asyncio.subprocess.Process] = {}

    async def run(
        self,
        command: str,
        cwd: str = "",
        timeout: int = 120,
        task_id: str = "unknown",
        env: Optional[dict] = None,
    ) -> dict:
        """Run a shell command and return the result.

        Returns:
            dict with keys: success (bool), output (str), exit_code (int | None)
        """
        work_dir = cwd if cwd and os.path.isdir(cwd) else None

        # Merge env
        run_env = {**os.environ}
        if env:
            run_env.update(env)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=work_dir,
                env=run_env,
            )
            self._active[task_id] = proc

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                self._active.pop(task_id, None)
                return {
                    "success": False,
                    "output": f"Timeout ({timeout}s) ejecutando: {command}",
                    "exit_code": -1,
                }

            self._active.pop(task_id, None)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            # Cap output size (avoid huge outputs crashing Telegram or WS)
            max_output = 50_000
            if len(output) > max_output:
                output = output[:max_output] + "\n\n... (output truncado)"

            return {
                "success": proc.returncode == 0,
                "output": output,
                "exit_code": proc.returncode,
            }

        except FileNotFoundError:
            return {
                "success": False,
                "output": f"Comando no encontrado: {command}",
                "exit_code": -1,
            }
        except Exception as e:
            logger.exception("Error running command: %s", command)
            return {
                "success": False,
                "output": f"Error ejecutando comando: {e}",
                "exit_code": -1,
            }

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running process by task ID."""
        proc = self._active.pop(task_id, None)
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
            logger.info("Cancelled process for task %s", task_id)
            return True
        return False

    async def cleanup(self) -> None:
        """Kill all active processes."""
        for task_id, proc in list(self._active.items()):
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
                logger.info("Cleaned up process for task %s", task_id)
        self._active.clear()
