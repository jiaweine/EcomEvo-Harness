from __future__ import annotations

import asyncio
import time

from ecomevo.api.durable_jobs import DurableConversationWorker


class SlowClaimStore:
    def claim_job(self, *_args, **_kwargs):
        time.sleep(0.08)
        return None


async def _unused_emit(*_args, **_kwargs):
    raise AssertionError("claim pressure test must not emit")


def test_slow_durable_job_claim_does_not_block_event_loop():
    async def exercise():
        worker = DurableConversationWorker(
            SlowClaimStore(),
            analyzer=None,
            mcp=None,
            emit=_unused_emit,
            wake=lambda _cid: None,
        )

        gaps: list[float] = []
        stop = asyncio.Event()

        async def heartbeat():
            loop = asyncio.get_running_loop()
            previous = loop.time()
            while not stop.is_set():
                await asyncio.sleep(0.005)
                now = loop.time()
                gaps.append(now - previous)
                previous = now

        heartbeat_task = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.01)
        claimed = await worker.run_once()
        await asyncio.sleep(0.01)
        stop.set()
        await heartbeat_task

        assert claimed is False
        assert gaps
        assert max(gaps) < 0.04

    asyncio.run(exercise())
