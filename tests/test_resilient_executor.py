import asyncio
import time
from types import SimpleNamespace

import pytest

from ecomevo.models import ToolCall
from ecomevo.runtime.resilient_executor import ResilientPTCExecutor


class AllowSandbox:
    def validate_tool(self, _tool):
        return SimpleNamespace(allowed=True, reason="", requires_confirmation=False)


class Registry:
    def __init__(self, tool):
        self.tools = {tool.key: tool}


class SlowTool:
    key = "slow.read"
    cost = 0.7

    async def execute(self, _ctx, _args):
        await asyncio.sleep(0.08)
        return {"ok": True}


@pytest.mark.asyncio
async def test_resilient_executor_turns_timeout_into_tool_result():
    executor = ResilientPTCExecutor(Registry(SlowTool()), AllowSandbox())
    executor.timeout_s = 0.02
    result = await executor.execute([
        ToolCall(call_id="c1", tool="slow.read", purpose="test", parallel_group="g")
    ], {})

    assert len(result) == 1
    assert result[0].ok is False
    assert result[0].error == "tool_timeout:0.02s"
    assert result[0].cost == 0.7
    assert result[0].duration_ms >= 15


class CountingTool:
    key = "count.read"
    cost = 0.1

    def __init__(self):
        self.active = 0
        self.peak = 0
        self.lock = asyncio.Lock()

    async def execute(self, _ctx, _args):
        async with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        async with self.lock:
            self.active -= 1
        return {"ok": True}


@pytest.mark.asyncio
async def test_resilient_executor_bounds_parallel_fanout():
    tool = CountingTool()
    executor = ResilientPTCExecutor(Registry(tool), AllowSandbox())
    executor.max_inflight = 2
    executor._slots = asyncio.Semaphore(2)
    executor.timeout_s = 1.0

    calls = [
        ToolCall(call_id=f"c{i}", tool="count.read", purpose="test", parallel_group="g")
        for i in range(8)
    ]
    results = await executor.execute(calls, {})

    assert all(result.ok for result in results)
    assert tool.peak == 2


@pytest.mark.asyncio
async def test_resilient_executor_timeout_includes_backpressure_wait():
    executor = ResilientPTCExecutor(Registry(SlowTool()), AllowSandbox())
    executor.timeout_s = 0.03
    executor.max_inflight = 1
    executor._slots = asyncio.Semaphore(1)
    calls = [
        ToolCall(call_id=f"c{i}", tool="slow.read", purpose="overload", parallel_group="g")
        for i in range(10)
    ]

    started = time.perf_counter()
    results = await executor.execute(calls, {})
    wall = time.perf_counter() - started

    assert all(result.error == "tool_timeout:0.03s" for result in results)
    assert wall < 0.15, "queued calls must share a bounded end-to-end deadline"
    assert max(result.duration_ms for result in results) < 150


def test_resilient_executor_uses_safe_defaults_for_malformed_environment(monkeypatch):
    monkeypatch.setenv("ECOMEVO_TOOL_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("ECOMEVO_TOOL_MAX_INFLIGHT", "not-an-integer")

    executor = ResilientPTCExecutor(Registry(SlowTool()), AllowSandbox())

    assert executor.timeout_s == 25.0
    assert executor.max_inflight == 16
