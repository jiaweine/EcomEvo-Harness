from __future__ import annotations

import asyncio
import threading
import time

from ecomevo.api import application


class SlowEmitStore:
    def __init__(self):
        self.job_thread: int | None = None
        self.event_thread: int | None = None

    def add_job_event(self, job_id, worker_id, event_type, payload):
        self.job_thread = threading.get_ident()
        time.sleep(0.08)
        return {
            "id": 1,
            "conversation_id": "conversation-pressure",
            "type": event_type,
            "payload": payload,
        }

    def add_event(self, conversation_id, event_type, payload):
        self.event_thread = threading.get_ident()
        time.sleep(0.08)
        return {
            "id": 2,
            "conversation_id": conversation_id,
            "type": event_type,
            "payload": payload,
        }


def test_async_emit_persists_off_event_loop_for_both_event_paths(monkeypatch):
    async def exercise():
        slow = SlowEmitStore()
        monkeypatch.setattr(application, "store", slow)
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        loop_thread = threading.get_ident()

        durable = await application.emit(
            "conversation-pressure",
            "planning.progress",
            {"detail": "pressure"},
            "job-pressure",
            "worker-pressure",
        )
        ordinary = await application.emit(
            "conversation-pressure",
            "notice",
            {"detail": "pressure"},
        )

        assert durable and durable["type"] == "planning.progress"
        assert ordinary and ordinary["type"] == "notice"
        assert slow.job_thread is not None and slow.job_thread != loop_thread
        assert slow.event_thread is not None and slow.event_thread != loop_thread

    asyncio.run(exercise())
