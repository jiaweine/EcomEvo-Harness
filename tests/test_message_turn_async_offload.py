from __future__ import annotations

import asyncio
import time

from fastapi import BackgroundTasks

from ecomevo.api import application


class SlowTurnStore:
    def get_conversation(self, cid):
        return {"id": cid, "scene": "merchant_review"}

    def list_messages(self, *_args, **_kwargs):
        return []

    def list_assets(self, *_args, **_kwargs):
        return []

    def claim_turn(self, _cid):
        time.sleep(0.08)
        return "lease-pressure"

    def accept_message_job(self, cid, **_kwargs):
        user = {"id": "msg-pressure", "conversation_id": cid, "role": "user", "content": "审核商家"}
        event = {"id": 1}
        job = {"id": "job-pressure"}
        return user, event, job

    def release_turn(self, *_args, **_kwargs):
        return None


class NoopWorker:
    async def run_once(self, *_args, **_kwargs):
        return False


def test_message_turn_claim_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowTurnStore())
        monkeypatch.setattr(application, "job_worker", NoopWorker())
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
        await application.conversation_message(
            "conversation-pressure",
            application.ChatRequest(content="审核商家", provider="auto"),
            BackgroundTasks(),
        )
        await asyncio.sleep(0.01)
        stop.set()
        await heartbeat_task

        assert gaps
        assert max(gaps) < 0.04

    asyncio.run(exercise())
