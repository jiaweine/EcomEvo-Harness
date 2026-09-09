from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.event_store import EventStore


class TracingBootstrapStore(BundledEventStore):
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


class SlowBootstrapStore(BundledEventStore):
    def _persist_bootstrap_group(self, batch):
        time.sleep(0.12)
        return super()._persist_bootstrap_group(batch)


class BlockingBootstrapGroupStore(BundledEventStore):
    def __init__(self, path):
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def _persist_bootstrap_group(self, batch):
        self.started.set()
        if not self.release.wait(timeout=3.0):
            raise TimeoutError("test did not release bootstrap group persistence")
        return super()._persist_bootstrap_group(batch)


class OverrideBootstrapStore(TracingBootstrapStore):
    def __init__(self, path):
        self.sync_bootstrap_calls = 0
        super().__init__(path)

    def create_session_events_checkpoint(self, *args, **kwargs):
        self.sync_bootstrap_calls += 1
        return super().create_session_events_checkpoint(*args, **kwargs)


def _events(index: int) -> list[tuple[str, dict]]:
    return [
        ("goal.parsed", {"goal": f"review-{index}"}),
        ("belief.updated", {"confidence": 0.2, "index": index}),
        ("harness.profile.bound", {"component_ids": ["active"]}),
    ]


def _snapshot(index: int) -> dict:
    return {"stage": "initial", "index": index}


def test_concurrent_async_bootstraps_share_one_durable_transaction(tmp_path):
    async def exercise():
        path = tmp_path / "grouped-bootstrap.db"
        store = TracingBootstrapStore(path)
        observer = EventStore(path)

        results = await asyncio.gather(
            *(
                store.create_session_events_checkpoint_async(
                    f"s-{index}",
                    _events(index),
                    _snapshot(index),
                    meta={"domain": "merchant_review", "index": index},
                )
                for index in range(32)
            )
        )

        assert store.immediate_begins == 1
        for index, (events, reference) in enumerate(results):
            sid = f"s-{index}"
            assert [event.seq for event in events] == [1, 2, 3]
            assert [event.session_id for event in events] == [sid, sid, sid]
            assert reference["session_id"] == sid
            assert reference["seq"] == 3
            assert reference["event_hash"] == events[-1].hash
            restored = observer.restore_checkpoint(sid)
            assert restored["index"] == index
            assert restored["_checkpoint"]["seq"] == 3
            assert observer.verify_chain(sid) is True

        with observer._conn() as connection:
            rows = connection.execute(
                "SELECT session_id,meta_json FROM sessions ORDER BY session_id"
            ).fetchall()
        assert len(rows) == 32
        assert all(json.loads(row["meta_json"])["domain"] == "merchant_review" for row in rows)

    asyncio.run(exercise())


def test_bootstrap_group_worker_bounds_large_batches(tmp_path):
    async def exercise():
        store = TracingBootstrapStore(tmp_path / "bounded-bootstrap.db")

        results = await asyncio.gather(
            *(
                store.create_session_events_checkpoint_async(
                    f"s-{index}",
                    _events(index),
                    _snapshot(index),
                )
                for index in range(130)
            )
        )

        assert store.immediate_begins == 3
        assert all([event.seq for event in events] == [1, 2, 3] for events, _ in results)
        assert all(reference["seq"] == 3 for _, reference in results)
        assert all(store.verify_chain(f"s-{index}") for index in range(130))

    asyncio.run(exercise())


def test_failed_bootstrap_group_rolls_back_then_isolates_valid_peers(tmp_path):
    async def exercise():
        store = TracingBootstrapStore(tmp_path / "bootstrap-isolation.db")
        store.create_session_events_checkpoint(
            "duplicate", _events(99), _snapshot(99), meta={"seed": True}
        )
        store.immediate_begins = 0

        results = await asyncio.gather(
            store.create_session_events_checkpoint_async(
                "good-a", _events(1), _snapshot(1), meta={"peer": "a"}
            ),
            store.create_session_events_checkpoint_async(
                "duplicate", _events(2), _snapshot(2), meta={"peer": "bad"}
            ),
            store.create_session_events_checkpoint_async(
                "good-b", _events(3), _snapshot(3), meta={"peer": "b"}
            ),
            return_exceptions=True,
        )

        assert not isinstance(results[0], Exception)
        assert isinstance(results[1], Exception)
        assert not isinstance(results[2], Exception)
        # One failed shared BEGIN plus three isolated atomic attempts.
        assert store.immediate_begins == 4
        assert store.restore_checkpoint("good-a")["index"] == 1
        assert store.restore_checkpoint("good-b")["index"] == 3
        assert len(store.list_events("good-a")) == 3
        assert len(store.list_events("good-b")) == 3
        assert store.verify_chain("good-a") is True
        assert store.verify_chain("good-b") is True
        assert store.restore_checkpoint("duplicate")["index"] == 99

    asyncio.run(exercise())


def test_bootstrap_group_sqlite_work_does_not_freeze_event_loop(tmp_path):
    async def exercise():
        store = SlowBootstrapStore(tmp_path / "bootstrap-off-loop.db")

        started = time.perf_counter()
        bootstrap_task = asyncio.create_task(
            store.create_session_events_checkpoint_async(
                "s1", _events(1), _snapshot(1)
            )
        )
        await asyncio.sleep(0.02)
        loop_delay = time.perf_counter() - started

        assert loop_delay < 0.08
        events, reference = await bootstrap_task
        assert [event.seq for event in events] == [1, 2, 3]
        assert reference["seq"] == 3
        assert store.verify_chain("s1") is True

    asyncio.run(exercise())


def test_bootstrap_caller_cancellation_waits_for_durable_persistence(tmp_path):
    async def exercise():
        path = tmp_path / "bootstrap-cancel.db"
        store = BlockingBootstrapGroupStore(path)
        observer = EventStore(path)
        task = asyncio.create_task(
            store.create_session_events_checkpoint_async(
                "cancelled", _events(7), _snapshot(7)
            )
        )

        for _ in range(300):
            if store.started.is_set():
                break
            await asyncio.sleep(0.001)
        assert store.started.is_set()

        task.cancel()
        store.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert observer.has_session("cancelled") is True
        assert observer.restore_checkpoint("cancelled")["index"] == 7
        assert len(observer.list_events("cancelled")) == 3
        assert observer.verify_chain("cancelled") is True

    asyncio.run(exercise())


def test_sync_bootstrap_override_keeps_existing_async_offload_contract(tmp_path):
    async def exercise():
        store = OverrideBootstrapStore(tmp_path / "bootstrap-override.db")

        results = await asyncio.gather(
            *(
                store.create_session_events_checkpoint_async(
                    f"s-{index}", _events(index), _snapshot(index)
                )
                for index in range(8)
            )
        )

        assert store.sync_bootstrap_calls == 8
        assert store.immediate_begins == 8
        assert all(reference["seq"] == 3 for _events_out, reference in results)
        assert all(store.verify_chain(f"s-{index}") for index in range(8))

    asyncio.run(exercise())
