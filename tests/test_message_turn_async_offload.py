from __future__ import annotations

import asyncio
import time

from fastapi import BackgroundTasks

from ecomevo.api import application


class SlowTurnStore:
    def __init__(self, slow_method: str):
        self.slow_method = slow_method

    def _stall(self, method: str) -> None:
        if self.slow_method == method:
            time.sleep(0.08)

    def get_conversation(self, cid):
        return {"id": cid, "scene": "merchant_review"}

    def list_messages(self, *_args, **_kwargs):
        return []

    def list_assets(self, *_args, **_kwargs):
        return []

    def claim_turn(self, _cid):
        self._stall("claim_turn")
        return "lease-pressure"

    def accept_message_job(self, cid, **_kwargs):
        self._stall("accept_message_job")
        user = {"id": "msg-pressure", "conversation_id": cid, "role": "user", "content": "审核商家"}
        event = {"id": 1}
        job = {"id": "job-pressure"}
        return user, event, job

    def release_turn(self, *_args, **_kwargs):
        return None


class NoopWorker:
    async def run_once(self, *_args, **_kwargs):
        return False


async def _max_message_loop_gap(monkeypatch, slow_method: str) -> float:
    monkeypatch.setattr(application, "store", SlowTurnStore(slow_method))
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
    return max(gaps)


def test_message_turn_claim_does_not_block_event_loop(monkeypatch):
    assert asyncio.run(_max_message_loop_gap(monkeypatch, "claim_turn")) < 0.04


def test_message_job_accept_does_not_block_event_loop(monkeypatch):
    assert asyncio.run(_max_message_loop_gap(monkeypatch, "accept_message_job")) < 0.04
