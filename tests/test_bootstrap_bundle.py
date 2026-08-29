from __future__ import annotations

import pytest

from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.engine import EcomEvoEngine


class TracingBundledEventStore(BundledEventStore):
    def __init__(self, path):
        self.immediate_begins = 0
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str):
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection


class SpyBundledEventStore(BundledEventStore):
    def __init__(self, path):
        self.bundle_calls = 0
        self.first_event_calls = 0
        super().__init__(path)

    def create_session_events_checkpoint(self, *args, **kwargs):
        self.bundle_calls += 1
        return super().create_session_events_checkpoint(*args, **kwargs)

    def create_session_and_append(self, *args, **kwargs):
        self.first_event_calls += 1
        return super().create_session_and_append(*args, **kwargs)


def test_bootstrap_bundle_uses_one_writer_transaction_and_binds_checkpoint(tmp_path):
    store = TracingBundledEventStore(tmp_path / "bundle.db")
    store.immediate_begins = 0
    events, checkpoint = store.create_session_events_checkpoint(
        "s1",
        [
            ("goal.parsed", {"goal": "review"}),
            ("belief.updated", {"confidence": 0.2}),
            ("harness.profile.bound", {"component_ids": ["a", "b"]}),
        ],
        {"stage": "initial", "belief": {"confidence": 0.2}},
        meta={"domain": "merchant_review"},
    )

    assert store.immediate_begins == 1
    assert [event.seq for event in events] == [1, 2, 3]
    assert [event.event_type for event in events] == [
        "goal.parsed",
        "belief.updated",
        "harness.profile.bound",
    ]
    assert events[0].prev_hash == "GENESIS"
    assert events[1].prev_hash == events[0].hash
    assert events[2].prev_hash == events[1].hash
    assert checkpoint["seq"] == 3
    assert checkpoint["event_hash"] == events[-1].hash
    restored = store.restore_checkpoint("s1")
    assert restored is not None
    assert restored["stage"] == "initial"
    assert restored["_checkpoint"]["event_hash"] == events[-1].hash
    assert store.verify_chain("s1") is True


def test_bootstrap_bundle_rolls_back_session_event_prefix_and_checkpoint(tmp_path):
    store = BundledEventStore(tmp_path / "rollback.db")
    circular: dict[str, object] = {}
    circular["self"] = circular

    with pytest.raises(ValueError):
        store.create_session_events_checkpoint(
            "broken",
            [
                ("goal.parsed", {"goal": "review"}),
                ("belief.updated", circular),
                ("harness.profile.bound", {"component_ids": []}),
            ],
            {"stage": "initial"},
            meta={"domain": "merchant_review"},
        )

    assert store.has_session("broken") is False
    assert store.list_events("broken") == []
    assert store.restore_checkpoint("broken") is None


@pytest.mark.asyncio
async def test_engine_uses_bundle_only_without_external_sink(tmp_path):
    sinkless_store = SpyBundledEventStore(tmp_path / "sinkless.db")
    sinkless_engine = EcomEvoEngine(
        tmp_path / "sinkless.db",
        plugin_overrides={"event.store": sinkless_store},
    )
    summary = await sinkless_engine.run(
        "审核商家并核对主体和授权资料",
        [],
        domain_hint="merchant_review",
    )
    assert summary.event_chain_valid is True
    assert sinkless_store.bundle_calls == 1
    assert sinkless_store.first_event_calls == 0

    streaming_store = SpyBundledEventStore(tmp_path / "streaming.db")
    streaming_engine = EcomEvoEngine(
        tmp_path / "streaming.db",
        plugin_overrides={"event.store": streaming_store},
    )
    seen: list[str] = []

    async def sink(event_type: str, _payload: dict):
        seen.append(event_type)

    streamed = await streaming_engine.run(
        "审核商家并核对主体和授权资料",
        [],
        sink=sink,
        domain_hint="merchant_review",
    )
    assert streamed.event_chain_valid is True
    assert streaming_store.bundle_calls == 0
    assert streaming_store.first_event_calls == 1
    assert seen[:3] == [
        "goal.parsed",
        "belief.updated",
        "harness.profile.bound",
    ]
