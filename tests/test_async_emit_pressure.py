from __future__ import annotations

import asyncio
import time

from ecomevo.api import application


class SlowEmitStore:
    def add_job_event(self, job_id, worker_id, event_type, payload):
        time.sleep(0.08)
        return {
            "id": 1,
            "conversation_id": "conversation-pressure",
            "type": event_type,
            "payload": payload,
        }


def test_durable_progress_emit_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowEmitStore())
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
        event = await application.emit(
            "conversation-pressure",
            "planning.progress",
            {"detail": "pressure"},
            "job-pressure",
            "worker-pressure",
        )
        await asyncio.sleep(0.01)
        stop.set()
        await heartbeat_task

        assert event and event["type"] == "planning.progress"
        assert gaps
        assert max(gaps) < 0.04

    asyncio.run(exercise())
