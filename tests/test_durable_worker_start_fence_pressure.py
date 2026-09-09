from __future__ import annotations

import asyncio
import time

from ecomevo.api.durable_jobs import DurableConversationWorker


class SlowJobFenceStore:
    def renew_job(self, *_args, **_kwargs):
        time.sleep(0.08)
        return False


class SlowTurnFenceStore:
    def renew_job(self, *_args, **_kwargs):
        return True

    def renew_or_restore_turn(self, *_args, **_kwargs):
        time.sleep(0.08)
        return False

    def finish_job_failure(self, *_args, **_kwargs):
        return None


async def _unused_emit(*_args, **_kwargs):
    raise AssertionError("start-fence pressure test must not emit")


async def _max_gap(store) -> float:
    worker = DurableConversationWorker(
        store,
        analyzer=None,
        mcp=None,
        emit=_unused_emit,
        wake=lambda _cid: None,
    )
    job = {
        "id": "job-pressure",
        "conversation_id": "conversation-pressure",
        "payload": {"lease_token": "lease-pressure"},
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
    await worker._execute(job)
    await asyncio.sleep(0.01)
    stop.set()
    await heartbeat_task
    assert gaps
    return max(gaps)


def test_slow_durable_job_start_fence_does_not_block_event_loop():
    assert asyncio.run(_max_gap(SlowJobFenceStore())) < 0.04


def test_slow_durable_turn_start_fence_does_not_block_event_loop():
    assert asyncio.run(_max_gap(SlowTurnFenceStore())) < 0.04
