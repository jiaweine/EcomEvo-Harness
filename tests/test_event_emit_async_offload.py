from __future__ import annotations

import asyncio
import time

from ecomevo.api import application


class SlowEmitStore:
    def __init__(self, slow_method: str):
        self.slow_method = slow_method

    def _event(self, cid, event_type, payload):
        time.sleep(0.08)
        return {"id": 1, "conversation_id": cid, "type": event_type, "payload": payload}

    def add_event(self, cid, event_type, payload):
        assert self.slow_method == "add_event"
        return self._event(cid, event_type, payload)

    def add_job_event(self, _job_id, _worker_id, event_type, payload):
        assert self.slow_method == "add_job_event"
        return self._event("conversation-pressure", event_type, payload)


async def _max_emit_loop_gap(monkeypatch, slow_method: str) -> float:
    monkeypatch.setattr(application, "store", SlowEmitStore(slow_method))
    monkeypatch.setattr(application, "wake", lambda _cid: None)

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
    if slow_method == "add_job_event":
        await application.emit(
            "conversation-pressure",
            "planning.progress",
            {"detail": "working"},
            "job-pressure",
            "worker-pressure",
        )
    else:
        await application.emit(
            "conversation-pressure",
            "notice",
            {"detail": "working"},
        )
    await asyncio.sleep(0.01)
    stop.set()
    await heartbeat_task
    assert gaps
    return max(gaps)


def test_durable_job_emit_does_not_block_event_loop(monkeypatch):
    assert asyncio.run(_max_emit_loop_gap(monkeypatch, "add_job_event")) < 0.04


def test_regular_emit_does_not_block_event_loop(monkeypatch):
    assert asyncio.run(_max_emit_loop_gap(monkeypatch, "add_event")) < 0.04
