from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.event_store import EventStore


class TracingAsyncStore(BundledEventStore):
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


class GateProbeStore(BundledEventStore):
    def __init__(self, path):
        self.active = 0
        self.peak_active = 0
        self._probe_lock = threading.Lock()
        super().__init__(path)

    def create_session_events_checkpoint(self, *args, **kwargs):
        with self._probe_lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.004)
            return super().create_session_events_checkpoint(*args, **kwargs)
        finally:
            with self._probe_lock:
                self.active -= 1


class BlockingBootstrapStore(BundledEventStore):
    def __init__(self, path):
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def create_session_events_checkpoint(self, *args, **kwargs):
        self.started.set()
        if not self.release.wait(timeout=3.0):
            raise TimeoutError("test did not release bootstrap")
        return super().create_session_events_checkpoint(*args, **kwargs)


class SpyAsyncStore(BundledEventStore):
    def __init__(self, path):
        self.async_bootstrap_calls = 0
        self.async_checkpoint_calls = 0
        self.async_verify_calls = 0
        super().__init__(path)

    async def create_session_events_checkpoint_async(self, *args, **kwargs):
        self.async_bootstrap_calls += 1
        return await super().create_session_events_checkpoint_async(*args, **kwargs)

    async def save_checkpoint_and_append_async(self, *args, **kwargs):
        self.async_checkpoint_calls += 1
        return await super().save_checkpoint_and_append_async(*args, **kwargs)

    async def verify_chain_async(self, *args, **kwargs):
        self.async_verify_calls += 1
        return await super().verify_chain_async(*args, **kwargs)


def _bootstrap_events():
    return [
        ("goal.parsed", {"goal": "review"}),
        ("belief.updated", {"confidence": 0.2}),
        ("harness.profile.bound", {"component_ids": ["a"]}),
    ]


def test_async_fast_paths_preserve_transactions_checkpoint_binding_and_chain(tmp_path):
    async def exercise():
        store = TracingAsyncStore(tmp_path / "async.db")
        events, initial = await store.create_session_events_checkpoint_async(
            "s1",
            _bootstrap_events(),
            {"stage": "initial"},
            meta={"domain": "merchant_review"},
        )
        first_ref, first_event = await store.save_checkpoint_and_append_async(
            "s1",
            {"stage": "after-first"},
            "runtime.checkpointed",
            {"stage": "after-first"},
        )
        second_ref, second_event = await store.save_checkpoint_and_append_async(
            "s1",
            {"stage": "after-second"},
            "runtime.checkpointed",
            {"stage": "after-second"},
        )
        valid = await store.verify_chain_async("s1")
        return store, events, initial, first_ref, first_event, second_ref, second_event, valid

    store, events, initial, first_ref, first_event, second_ref, second_event, valid = asyncio.run(exercise())
    assert store.immediate_begins == 3
    assert [event.seq for event in events] == [1, 2, 3]
    assert initial["seq"] == 3
    assert first_ref["seq"] == 3
    assert first_event.seq == 4
    assert first_event.prev_hash == events[-1].hash
    assert second_ref["seq"] == 4
    assert second_event.seq == 5
    assert second_event.prev_hash == first_event.hash
    assert valid is True
    assert store.verify_chain("s1") is True


def test_async_io_gate_queues_before_executor_and_allows_only_one_store_operation(tmp_path):
    async def exercise():
        store = GateProbeStore(tmp_path / "gate.db")
        await asyncio.gather(
            *(
                store.create_session_events_checkpoint_async(
                    f"s{index}",
                    _bootstrap_events(),
                    {"stage": "initial", "index": index},
                )
                for index in range(12)
            )
        )
        return store

    store = asyncio.run(exercise())
    assert store.peak_active == 1
    assert all(store.verify_chain(f"s{index}") for index in range(12))


def test_cancellation_after_worker_start_waits_for_durable_outcome(tmp_path):
    async def exercise():
        store = BlockingBootstrapStore(tmp_path / "cancel.db")
        task = asyncio.create_task(
            store.create_session_events_checkpoint_async(
                "cancelled",
                _bootstrap_events(),
                {"stage": "initial"},
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
        return store

    store = asyncio.run(exercise())
    assert store.has_session("cancelled") is True
    assert store.verify_chain("cancelled") is True
    assert store.restore_checkpoint("cancelled")["stage"] == "initial"


def test_engine_uses_async_fast_paths_only_for_sinkless_builtin_store(tmp_path):
    async def exercise():
        sinkless_store = SpyAsyncStore(tmp_path / "sinkless.db")
        sinkless_engine = EcomEvoEngine(
            tmp_path / "sinkless.db",
            plugin_overrides={"event.store": sinkless_store},
        )
        sinkless = await sinkless_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint="merchant_review",
        )
        assert sinkless.event_chain_valid is True
        assert sinkless_store.async_bootstrap_calls == 1
        assert sinkless_store.async_checkpoint_calls > 0
        assert sinkless_store.async_verify_calls == 1

        streaming_store = SpyAsyncStore(tmp_path / "streaming.db")
        streaming_engine = EcomEvoEngine(
            tmp_path / "streaming.db",
            plugin_overrides={"event.store": streaming_store},
        )
        seen: list[str] = []

        async def sink(event_type: str, _payload: dict):
            seen.append(event_type)

        streaming = await streaming_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            sink=sink,
            domain_hint="merchant_review",
        )
        assert streaming.event_chain_valid is True
        assert streaming_store.async_bootstrap_calls == 0
        assert streaming_store.async_checkpoint_calls == 0
        assert streaming_store.async_verify_calls == 0
        assert seen[:3] == ["goal.parsed", "belief.updated", "harness.profile.bound"]

        base_store = EventStore(tmp_path / "base.db")
        base_engine = EcomEvoEngine(
            tmp_path / "base.db",
            plugin_overrides={"event.store": base_store},
        )
        base = await base_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint="merchant_review",
        )
        assert base.event_chain_valid is True

    asyncio.run(exercise())
