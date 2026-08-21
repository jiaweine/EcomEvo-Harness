from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

from ecomevo.models import ToolCall, ToolResult
from .tools import PTCExecutor, ToolRegistry
from .sandbox import ActionSandbox


def _bounded_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class ResilientPTCExecutor(PTCExecutor):
    """PTC executor with runtime-wide backpressure and bounded read-tool tail latency.

    The semaphore is shared by all tasks using the Engine instance, so a burst of
    autonomous tasks cannot create unbounded local/MCP fan-out. Timeouts are returned as
    ordinary failed ToolResult objects; they are evidence acquisition failures, never a
    reason to bypass the verifier or retry side effects.
    """

    def __init__(self, registry: ToolRegistry, sandbox: ActionSandbox):
        super().__init__(registry, sandbox)
        self.timeout_s = _bounded_float_env("ECOMEVO_TOOL_TIMEOUT_SECONDS", 25.0, 2.0, 120.0)
        self.max_inflight = _bounded_int_env("ECOMEVO_TOOL_MAX_INFLIGHT", 16, 1, 64)
        self._slots = asyncio.Semaphore(self.max_inflight)

    async def _one(self, call: ToolCall, ctx: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        try:
            # The deadline starts before semaphore acquisition. Otherwise an overloaded
            # caller can wait indefinitely in the queue and then receive a fresh timeout,
            # multiplying tail latency by the number of queued calls.
            async with asyncio.timeout(self.timeout_s):
                async with self._slots:
                    return await super()._one(call, ctx)
        except TimeoutError:
            tool = self.registry.tools.get(call.tool)
            cost = float(getattr(tool, "cost", 0.0) or 0.0) if tool is not None else 0.0
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                ok=False,
                error=f"tool_timeout:{self.timeout_s:g}s",
                cost=cost,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
