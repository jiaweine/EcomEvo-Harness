from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.event_store import EventStore
from ecomevo.runtime.grouped_restore_event_store import GroupedRestoreBundledEventStore


class TracingRestoreStore(GroupedRestoreBundledEventStore):
    def __init__(self, path):
        self.restore_batches: list[int] = []
        self.restore_connections = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _restore_checkpoint_group(self, batch):
        with self._trace_lock:
            self.restore_batches.append(len(batch))
        return super()._restore_checkpoint_group(batch)

    def _conn(self):
        connection = super()._conn()
        return connection


class CountingConnectionRestoreStore(TracingRestoreStore):
    def _restore_checkpoint_group(self, batch):
        with self._trace_lock:
            self.restore_connections += 1
        return super()._restore_checkpoint_group(batch)


class SlowRestoreStore(GroupedRestoreBundledEventStore):
    def _restore_checkpoint_group(self, batch):
        time.sleep(0.12)
        return super()._restore_checkpoint_group(batch)


class BlockingRestoreStore(GroupedRestoreBundledEventStore):
    def __init__(self, path):
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def _restore_checkpoint_group(self, batch):
        self.started.set()
        if not self.release.wait(timeout=3.0):
            raise TimeoutError("test did not release grouped restore")
        return super()._restore_checkpoint_group(batch)


class OverrideRestoreStore(GroupedRestoreBundledEventStore):
    def __init__(self, path):
        self.sync_restore_calls = 0
        self.group_calls = 0
        super().__init__(path)

    def restore_checkpoint(self, *args, **kwargs):
        self.sync_restore_calls += 1
        return super().restore_checkpoint(*args, **kwargs)

    def _restore_checkpoint_group(self, batch):
        self.group_calls += 1
        return super()._restore_checkpoint_group(batch)


def _seed(store: EventStore, count: int = 140) -> None:
    for index in range(count):
        sid = f"s-{index}"
        store.create_session_events_checkpoint(
            sid,
            [
                ("goal.parsed", {"goal": f"review-{index}"}),
                ("belief.updated", {"index": index}),
                ("harness.profile.bound", {"component_ids": ["active"]}),
            ],
            {"stage": "initial", "index": index},
        )
        event4 = store.append(sid, "tool.executed", {"index": index})
        store.save_checkpoint(
            sid,
            {"stage": "recovery-1", "index": index},
            seq=event4.seq,
        )
        event5 = store.append(sid, "verification.completed", {"index": index})
        store.save_checkpoint(
            sid,
            {"stage": "recovery-2", "index": index},
            seq=event5.seq,
        )


def _projection(value):
    if value is None:
        return None
    return {
        "stage": value.get("stage"),
        "index": value.get("index"),
        "checkpoint": dict(value.get("_checkpoint") or {}),
    }


def test_32_concurrent_exact_restores_share_one_read_batch(tmp_path):
    async def exercise():
        path = tmp_path / "grouped-restore.db"
        store = CountingConnectionRestoreStore(path)
        _seed(store, 32)

        results = await asyncio.gather(
            *(store.restore_checkpoint_async(f"s-{index}", 4) for index in range(32))
        )

        assert store.restore_batches == [32]
        assert store.restore_connections == 1
        for index, value in enumerate(results):
            assert value is not None
            assert value["stage"] == "recovery-1"
            assert value["index"] == index
            assert value["_checkpoint"]["seq"] == 4

    asyncio.run(exercise())


def test_large_restore_group_is_bounded_to_64(tmp_path):
    async def exercise():
        store = CountingConnectionRestoreStore(tmp_path / "bounded-restore.db")
        _seed(store, 130)

        results = await asyncio.gather(
            *(store.restore_checkpoint_async(f"s-{index}", 5) for index in range(130))
        )

        assert store.restore_batches == [64, 64, 2]
        assert store.restore_connections == 3
        assert all(value is not None for value in results)
        assert all(value["_checkpoint"]["seq"] == 5 for value in results)

    asyncio.run(exercise())


def test_grouped_restore_matches_sync_integrity_semantics(tmp_path):
    async def exercise():
        path = tmp_path / "restore-semantics.db"
        store = GroupedRestoreBundledEventStore(path)
        _seed(store, 3)

        cases = [("s-0", 3), ("s-0", 4), ("s-0", 5), ("missing", 5)]
        for sid, seq in cases:
            expected = _projection(store.restore_checkpoint(sid, seq))
            actual = _projection(await store.restore_checkpoint_async(sid, seq))
            assert actual == expected

        store.create_session_events_checkpoint(
            "bad-state", [("goal.parsed", {"goal": "bad-state"})], {"kind": "state"}
        )
        store.create_session_events_checkpoint(
            "bad-event", [("goal.parsed", {"goal": "bad-event"})], {"kind": "event"}
        )
        with store._conn() as connection:
            connection.execute(
                "UPDATE snapshots SET state_hash='corrupt' WHERE session_id='bad-state'"
            )
            connection.execute(
                "UPDATE snapshots SET event_hash='corrupt' WHERE session_id='bad-event'"
            )

        assert store.restore_checkpoint("bad-state", 1) is None
        assert store.restore_checkpoint("bad-event", 1) is None
        bad_state, bad_event = await asyncio.gather(
            store.restore_checkpoint_async("bad-state", 1),
            store.restore_checkpoint_async("bad-event", 1),
        )
        assert bad_state is None
        assert bad_event is None

    asyncio.run(exercise())


def test_failed_shared_read_isolates_malformed_checkpoint(tmp_path):
    async def exercise():
        path = tmp_path / "restore-isolation.db"
        store = GroupedRestoreBundledEventStore(path)
        _seed(store, 3)
        with store._conn() as connection:
            connection.execute(
                "UPDATE snapshots SET snapshot_blob='json:{bad' WHERE session_id='s-1' AND seq=4"
            )

        results = await asyncio.gather(
            store.restore_checkpoint_async("s-0", 4),
            store.restore_checkpoint_async("s-1", 4),
            store.restore_checkpoint_async("s-2", 4),
            return_exceptions=True,
        )

        assert results[0]["index"] == 0
        assert isinstance(results[1], json.JSONDecodeError)
        assert results[2]["index"] == 2

    asyncio.run(exercise())


def test_grouped_restore_sqlite_work_does_not_freeze_event_loop(tmp_path):
    async def exercise():
        store = SlowRestoreStore(tmp_path / "restore-off-loop.db")
        _seed(store, 1)

        started = time.perf_counter()
        restore_task = asyncio.create_task(store.restore_checkpoint_async("s-0", 4))
        await asyncio.sleep(0.02)
        loop_delay = time.perf_counter() - started

        assert loop_delay < 0.08
        value = await restore_task
        assert value["stage"] == "recovery-1"

    asyncio.run(exercise())


def test_cancelled_restore_caller_does_not_poison_peer(tmp_path):
    async def exercise():
        store = BlockingRestoreStore(tmp_path / "restore-cancel.db")
        _seed(store, 2)

        cancelled = asyncio.create_task(store.restore_checkpoint_async("s-0", 4))
        peer = asyncio.create_task(store.restore_checkpoint_async("s-1", 4))
        for _ in range(300):
            if store.started.is_set():
                break
            await asyncio.sleep(0.001)
        assert store.started.is_set()

        cancelled.cancel()
        store.release.set()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        value = await peer
        assert value["index"] == 1
        assert value["_checkpoint"]["seq"] == 4

    asyncio.run(exercise())


def test_sync_restore_override_keeps_compatibility_path(tmp_path):
    async def exercise():
        store = OverrideRestoreStore(tmp_path / "restore-override.db")
        _seed(store, 8)
        store.sync_restore_calls = 0

        results = await asyncio.gather(
            *(store.restore_checkpoint_async(f"s-{index}", 4) for index in range(8))
        )

        assert store.sync_restore_calls == 8
        assert store.group_calls == 0
        assert all(value["_checkpoint"]["seq"] == 4 for value in results)

    asyncio.run(exercise())


def test_default_engine_uses_grouped_restore_store_without_changing_plugin_contract(tmp_path):
    engine = EcomEvoEngine(tmp_path / "engine-grouped-restore.db")
    assert isinstance(engine.events, GroupedRestoreBundledEventStore)

    injected = EventStore(tmp_path / "engine-injected-store.db")
    overridden = EcomEvoEngine(
        tmp_path / "engine-other.db",
        plugin_overrides={"event.store": injected},
    )
    assert overridden.events is injected
    assert not hasattr(injected, "restore_checkpoint_async")
