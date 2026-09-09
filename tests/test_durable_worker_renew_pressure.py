from __future__ import annotations

import asyncio
import time

from ecomevo.api.durable_jobs import DurableConversationWorker


class SlowRenewStore:
    def renew_job(self, *_args, **_kwargs):
        time.sleep(0.08)
        return False

    def renew_or_restore_turn(self, *_args, **_kwargs):
        return True


async def _unused_emit(*_args, **_kwargs):
    raise AssertionError("renewal pressure test must not emit")


def test_slow_durable_lease_renewal_does_not_block_event_loop():
    async def exercise():
        worker = DurableConversationWorker(
            SlowRenewStore(),
            analyzer=None,
            mcp=None,
            emit=_unused_emit,
            wake=lambda _cid: None,
        )
        worker.renew_interval_seconds = 0.001
        job = {
            "id": "job-pressure",
            "conversation_id": "conversation-pressure",
            "turn_token": "lease-pressure",
        }

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
        await worker._renew(job)
        await asyncio.sleep(0.01)
        stop.set()
        await heartbeat_task

        assert gaps
        assert max(gaps) < 0.04

    asyncio.run(exercise())
