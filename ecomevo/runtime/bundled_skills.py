from __future__ import annotations

import asyncio
import threading
import weakref
from typing import Callable, TypeVar

from .skills import AdaptiveSkillLibrary


_T = TypeVar("_T")


class BundledAdaptiveSkillLibrary(AdaptiveSkillLibrary):
    """Built-in skill-library finalization fast path without changing the base contract."""

    def __init__(self, db_path):
        self._async_gate_lock = threading.RLock()
        self._async_gates: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()
        super().__init__(db_path)

    def _async_gate(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        with self._async_gate_lock:
            gate = self._async_gates.get(loop)
            if gate is None:
                gate = asyncio.Lock()
                self._async_gates[loop] = gate
            return gate

    async def _run_io(self, call: Callable[..., _T], /, *args, **kwargs) -> _T:
        """Run one skill-library SQLite operation off-loop with clear cancellation."""
        loop = asyncio.get_running_loop()
        gate = self._async_gate(loop)
        async with gate:
            task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError as cancelled:
                try:
                    await asyncio.shield(task)
                except Exception:
                    raise
                raise cancelled

    async def note_run_async(
        self,
        domain: str,
        *,
        success: bool,
        skill_used: bool = False,
    ) -> None:
        await self._run_io(
            self.note_run,
            domain,
            success=success,
            skill_used=skill_used,
        )
