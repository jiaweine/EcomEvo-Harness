from __future__ import annotations

import asyncio
import time

from fastapi import BackgroundTasks, WebSocketDisconnect

from ecomevo.api import application


class SlowBoundaryStore:
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
        return "lease-pressure"

    def accept_message_job(self, cid, **_kwargs):
        return (
            {"id": "msg-pressure", "conversation_id": cid, "role": "user", "content": "审核商家"},
            {"id": 1},
            {"id": "job-pressure"},
        )

    def get_action(self, action_id):
        return {"id": action_id, "conversation_id": "conversation-pressure", "side_effect": {}}

    def transition_action_with_event(self, action_id, *_args, **_kwargs):
        self._stall("transition_action_with_event")
        return ({"id": action_id, "status": "rejected"}, {"id": 1})


class NoopWorker:
    async def run_once(self, *_args, **_kwargs):
        return False


class SlowWebSocketStore:
    def get_conversation(self, cid):
        return {"id": cid, "scene": "merchant_review"}

    def list_events(self, *_args, **_kwargs):
        time.sleep(0.08)
        raise WebSocketDisconnect()


class FakeWebSocket:
    async def accept(self):
        return None

    async def send_json(self, _payload):
        return None

    async def close(self, **_kwargs):
        return None


async def _max_loop_gap(coro) -> float:
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
    await coro
    await asyncio.sleep(0.01)
    stop.set()
    await heartbeat_task
    assert gaps
    return max(gaps)


def test_message_asset_binding_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("bind_assets_atomically")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "job_worker", NoopWorker())
        monkeypatch.setattr(application, "wake", lambda _cid: None)

        def slow_bind(_store, _asset_ids, _cid):
            store._stall("bind_assets_atomically")

        monkeypatch.setattr(application, "bind_assets_atomically", slow_bind)

        await application.conversation_message(
            "conversation-pressure",
            application.ChatRequest(content="审核商家", provider="auto"),
            BackgroundTasks(),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_action_rejection_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("transition_action_with_event")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        await application.action_decide(
            "action-pressure",
            application.ActionDecision(decision="reject"),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_websocket_event_polling_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowWebSocketStore())
        await application.conversation_ws(FakeWebSocket(), "conversation-pressure")

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04
