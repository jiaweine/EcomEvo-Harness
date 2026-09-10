from __future__ import annotations

import asyncio
import time
from io import BytesIO

from fastapi import BackgroundTasks, UploadFile, WebSocketDisconnect

from ecomevo.api import application


class SlowBoundaryStore:
    def __init__(self, slow_method: str):
        self.slow_method = slow_method

    def _stall(self, method: str) -> None:
        if self.slow_method == method:
            time.sleep(0.08)

    def get_conversation(self, cid):
        self._stall("get_conversation")
        return {"id": cid, "scene": "merchant_review"}

    def list_messages(self, *_args, **_kwargs):
        self._stall("list_messages")
        return []

    def list_assets(self, *_args, **_kwargs):
        self._stall("list_assets")
        return []

    def get_asset(self, asset_id):
        self._stall("get_asset")
        return {
            "id": asset_id,
            "conversation_id": "conversation-pressure",
            "active": True,
            "name": "asset-pressure",
            "mime": "text/plain",
            "path": application.__file__,
            "meta": {},
        }

    def claim_turn(self, _cid):
        return "lease-pressure"

    def accept_message_job(self, cid, **_kwargs):
        return (
            {"id": "msg-pressure", "conversation_id": cid, "role": "user", "content": "审核商家"},
            {"id": 1},
            {"id": "job-pressure"},
        )

    def get_action(self, action_id):
        self._stall("get_action")
        return {"id": action_id, "conversation_id": "conversation-pressure", "side_effect": {}}

    def transition_action_with_event(self, action_id, *_args, **_kwargs):
        self._stall("transition_action_with_event")
        return ({"id": action_id, "status": "rejected"}, {"id": 1})


class SlowAssetStore:
    def __init__(self, slow_method: str):
        self.slow_method = slow_method

    def _stall(self, method: str) -> None:
        if self.slow_method == method:
            time.sleep(0.08)

    def get_asset(self, asset_id):
        self._stall("get_asset")
        return {
            "id": asset_id,
            "conversation_id": "conversation-pressure",
            "active": True,
            "name": "asset-pressure",
            "mime": "text/plain",
            "path": "/tmp/asset-pressure.txt",
            "meta": {},
        }

    def has_active_turn(self, _cid):
        self._stall("has_active_turn")
        return False

    def set_asset_active(self, asset_id, active, reason):
        self._stall("set_asset_active")
        return {"id": asset_id, "active": active, "excluded_reason": reason, "name": "asset-pressure"}

    def delete_asset_if_unreferenced(self, asset_id):
        self._stall("delete_asset_if_unreferenced")
        return {"id": asset_id, "name": "asset-pressure"}


class SlowUploadStore:
    def __init__(self, asset_dir):
        self.asset_dir = asset_dir

    def get_conversation(self, cid):
        return {"id": cid, "scene": "merchant_review"}

    def has_active_turn(self, _cid):
        return False

    def list_assets(self, *_args, **_kwargs):
        return []

    def add_asset(self, cid, **kwargs):
        time.sleep(0.08)
        return {"id": "asset-pressure", "conversation_id": cid, **kwargs}


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


def test_asset_scope_update_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowAssetStore("set_asset_active"))
        monkeypatch.setattr(application, "emit", lambda *_args, **_kwargs: asyncio.sleep(0))
        await application.asset_scope(
            "asset-pressure",
            application.AssetScopePatch(active=False, reason="stress"),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_asset_delete_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowAssetStore("delete_asset_if_unreferenced"))
        monkeypatch.setattr(application, "emit", lambda *_args, **_kwargs: asyncio.sleep(0))
        await application.asset_delete("asset-pressure")

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_asset_upload_store_write_does_not_block_event_loop(tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowUploadStore(tmp_path))
        monkeypatch.setattr(application, "_normalize_upload_type", lambda *_args: (".txt", "text/plain"))
        monkeypatch.setattr(application, "_validate_uploaded_file", lambda *_args: None)
        monkeypatch.setattr(application, "probe_media", lambda *_args: {})
        monkeypatch.setattr(application, "_public_asset", lambda row: row)
        upload = UploadFile(filename="asset.txt", file=BytesIO(b"pressure"))
        await application.asset_upload(upload, "conversation-pressure")

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_message_conversation_lookup_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("get_conversation")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "job_worker", NoopWorker())
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        monkeypatch.setattr(application, "bind_assets_atomically", lambda *_args: None)
        await application.conversation_message(
            "conversation-pressure",
            application.ChatRequest(content="审核商家", provider="auto"),
            BackgroundTasks(),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_message_history_lookup_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("list_messages")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "job_worker", NoopWorker())
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        monkeypatch.setattr(application, "bind_assets_atomically", lambda *_args: None)
        await application.conversation_message(
            "conversation-pressure",
            application.ChatRequest(content="审核商家", provider="auto"),
            BackgroundTasks(),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_message_asset_listing_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("list_assets")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "job_worker", NoopWorker())
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        monkeypatch.setattr(application, "bind_assets_atomically", lambda *_args: None)
        await application.conversation_message(
            "conversation-pressure",
            application.ChatRequest(content="审核商家", provider="auto"),
            BackgroundTasks(),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_message_asset_lookup_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("get_asset")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "job_worker", NoopWorker())
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        monkeypatch.setattr(application, "bind_assets_atomically", lambda *_args: None)
        await application.conversation_message(
            "conversation-pressure",
            application.ChatRequest(content="审核商家", provider="auto", asset_ids=["asset-pressure"]),
            BackgroundTasks(),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_asset_scope_lookup_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowAssetStore("get_asset"))
        monkeypatch.setattr(application, "emit", lambda *_args, **_kwargs: asyncio.sleep(0))
        await application.asset_scope(
            "asset-pressure",
            application.AssetScopePatch(active=False, reason="stress"),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_asset_scope_turn_check_does_not_block_event_loop(monkeypatch):
    async def exercise():
        monkeypatch.setattr(application, "store", SlowAssetStore("has_active_turn"))
        monkeypatch.setattr(application, "emit", lambda *_args, **_kwargs: asyncio.sleep(0))
        await application.asset_scope(
            "asset-pressure",
            application.AssetScopePatch(active=False, reason="stress"),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04


def test_action_lookup_does_not_block_event_loop(monkeypatch):
    async def exercise():
        store = SlowBoundaryStore("get_action")
        monkeypatch.setattr(application, "store", store)
        monkeypatch.setattr(application, "wake", lambda _cid: None)
        await application.action_decide(
            "action-pressure",
            application.ActionDecision(decision="reject"),
        )

    assert asyncio.run(_max_loop_gap(exercise())) < 0.04
