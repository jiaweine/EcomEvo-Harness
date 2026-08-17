from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ecomevo.models import ToolCall, ToolResult
from .tools import PTCExecutor, ToolRegistry
from .sandbox import ActionSandbox


class ResilientPTCExecutor(PTCExecutor):
    """PTC executor with runtime-wide backpressure and bounded read-tool tail latency.

    The semaphore is shared by all tasks using the Engine instance, so a burst of
    autonomous tasks cannot create unbounded local/MCP fan-out. Timeouts are returned as
    ordinary failed ToolResult objects; they are evidence acquisition failures, never a
    reason to bypass the verifier or retry side effects.
    """

    def __init__(self, registry: ToolRegistry, sandbox: ActionSandbox):
        super().__init__(registry, sandbox)
        self.timeout_s = max(2.0, min(120.0, float(os.environ.get("ECOMEVO_TOOL_TIMEOUT_SECONDS", "25"))))
        self.max_inflight = max(1, min(64, int(os.environ.get("ECOMEVO_TOOL_MAX_INFLIGHT", "16"))))
        self._slots = asyncio.Semaphore(self.max_inflight)

    async def _one(self, call: ToolCall, ctx: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        async with self._slots:
            try:
                return await asyncio.wait_for(super()._one(call, ctx), timeout=self.timeout_s)
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
