from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.event_store import EventStore


class TracingCheckpointStore(BundledEventStore):
    def __init__(self, path):
        self.immediate_begins = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str):
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._trace_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection


class SlowCheckpointStore(BundledEventStore):
    def _persist_checkpoint_group(self, batch):
        time.sleep(0.12)
        return super()._persist_checkpoint_group(batch)


def _seed(store: BundledEventStore, session_id: str) -> None:
    store.create_session(session_id)
    event = store.append(session_id, "seed", {"session_id": session_id})
    assert event.seq == 1


def test_concurrent_async_checkpoints_share_one_durable_transaction(tmp_path):
    async def exercise():
        path = tmp_path / "grouped-checkpoints.db"
        store = TracingCheckpointStore(path)
        observer = EventStore(path)
        session_ids = [f"s-{index}" for index in range(32)]
        for sid in session_ids:
            _seed(store, sid)
        store.immediate_begins = 0

        results = await asyncio.gather(
            *(
                store.save_checkpoint_and_append_async(
                    sid,
                    {"index": index, "stage": "recovery"},
                    "runtime.checkpointed",
                    {"stage": "recovery"},
                )
                for index, sid in enumerate(session_ids)
            )
        )

        assert store.immediate_begins == 1
        for index, (reference, event) in enumerate(results):
            sid = session_ids[index]
            assert reference["seq"] == 1
            assert event.session_id == sid
            assert event.seq == 2
            assert event.payload["seq"] == 1
            assert event.payload["event_hash"] == reference["event_hash"]
            assert observer.restore_checkpoint(sid)["index"] == index
            assert len(observer.list_events(sid)) == 2
            assert observer.verify_chain(sid) is True

    asyncio.run(exercise())


def test_grouped_async_checkpoints_preserve_same_session_pre_audit_order(tmp_path):
    async def exercise():
        store = TracingCheckpointStore(tmp_path / "same-session-checkpoints.db")
        _seed(store, "shared")
        store.immediate_begins = 0

        results = await asyncio.gather(
            *(
                store.save_checkpoint_and_append_async(
                    "shared",
                    {"round": index},
                    "runtime.checkpointed",
                    {"stage": f"round-{index}"},
                )
                for index in range(12)
            )
        )

        assert store.immediate_begins == 1
        assert [reference["seq"] for reference, _event in results] == list(range(1, 13))
        assert [event.seq for _reference, event in results] == list(range(2, 14))
        events = store.list_events("shared")
        assert [event.payload.get("stage") for event in events[1:]] == [
            f"round-{index}" for index in range(12)
        ]
        for reference, event in results:
            assert event.payload["seq"] == reference["seq"]
            assert event.payload["event_hash"] == reference["event_hash"]
        assert store.restore_checkpoint("shared")["round"] == 11
        assert store.verify_chain("shared") is True

    asyncio.run(exercise())


def test_checkpoint_group_worker_bounds_large_batches(tmp_path):
    async def exercise():
        store = TracingCheckpointStore(tmp_path / "bounded-checkpoints.db")
        session_ids = [f"s-{index}" for index in range(130)]
        for sid in session_ids:
            _seed(store, sid)
        store.immediate_begins = 0

        results = await asyncio.gather(
            *(
                store.save_checkpoint_and_append_async(
                    sid,
                    {"index": index},
                    "runtime.checkpointed",
                    {"stage": "bounded"},
                )
                for index, sid in enumerate(session_ids)
            )
        )

        assert store.immediate_begins == 3
        assert all(reference["seq"] == 1 for reference, _event in results)
        assert all(event.seq == 2 for _reference, event in results)
        assert all(store.verify_chain(sid) for sid in session_ids)

    asyncio.run(exercise())


def test_failed_checkpoint_group_rolls_back_then_isolates_valid_peers(tmp_path):
    async def exercise():
        store = TracingCheckpointStore(tmp_path / "checkpoint-isolation.db")
        _seed(store, "good-a")
        _seed(store, "good-b")
        store.immediate_begins = 0

        results = await asyncio.gather(
            store.save_checkpoint_and_append_async(
                "good-a", {"value": "a"}, "runtime.checkpointed", {"stage": "a"}
            ),
            store.save_checkpoint_and_append_async(
                "missing", {"value": "bad"}, "runtime.checkpointed", {"stage": "bad"}
            ),
            store.save_checkpoint_and_append_async(
                "good-b", {"value": "b"}, "runtime.checkpointed", {"stage": "b"}
            ),
            return_exceptions=True,
        )

        assert not isinstance(results[0], Exception)
        assert isinstance(results[1], KeyError)
        assert not isinstance(results[2], Exception)
        assert store.immediate_begins == 4
        assert store.restore_checkpoint("good-a")["value"] == "a"
        assert store.restore_checkpoint("good-b")["value"] == "b"
        assert len(store.list_events("good-a")) == 2
        assert len(store.list_events("good-b")) == 2
        assert store.verify_chain("good-a") is True
        assert store.verify_chain("good-b") is True

    asyncio.run(exercise())


def test_checkpoint_group_sqlite_work_does_not_freeze_event_loop(tmp_path):
    async def exercise():
        store = SlowCheckpointStore(tmp_path / "checkpoint-off-loop.db")
        _seed(store, "s1")

        started = time.perf_counter()
        checkpoint_task = asyncio.create_task(
            store.save_checkpoint_and_append_async(
                "s1",
                {"value": 1},
                "runtime.checkpointed",
                {"stage": "slow"},
            )
        )
        await asyncio.sleep(0.02)
        loop_delay = time.perf_counter() - started

        assert loop_delay < 0.08
        reference, event = await checkpoint_task
        assert reference["seq"] == 1
        assert event.seq == 2
        assert store.verify_chain("s1") is True

    asyncio.run(exercise())


def test_checkpoint_caller_cancellation_waits_for_durable_persistence(tmp_path):
    async def exercise():
        path = tmp_path / "checkpoint-cancel.db"
        store = SlowCheckpointStore(path)
        observer = EventStore(path)
        _seed(store, "s1")

        task = asyncio.create_task(
            store.save_checkpoint_and_append_async(
                "s1",
                {"value": "durable"},
                "runtime.checkpointed",
                {"stage": "cancel"},
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert observer.restore_checkpoint("s1")["value"] == "durable"
        events = observer.list_events("s1")
        assert len(events) == 2
        assert events[-1].event_type == "runtime.checkpointed"
        assert events[-1].payload["seq"] == 1
        assert observer.verify_chain("s1") is True

    asyncio.run(exercise())
