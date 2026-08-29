from __future__ import annotations

import asyncio

from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.event_store import EventStore


class TracingGroupedStore(BundledEventStore):
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


class SpyGroupedStore(BundledEventStore):
    def __init__(self, path):
        self.grouped_calls = 0
        self.sync_append_calls = 0
        super().__init__(path)

    async def append_grouped(self, *args, **kwargs):
        self.grouped_calls += 1
        return await super().append_grouped(*args, **kwargs)

    def append(self, *args, **kwargs):
        self.sync_append_calls += 1
        return super().append(*args, **kwargs)


def test_concurrent_grouped_appends_share_one_durable_transaction(tmp_path):
    async def exercise():
        store = TracingGroupedStore(tmp_path / "group.db")
        session_ids = [f"s-{index}" for index in range(32)]
        for sid in session_ids:
            store.create_session(sid)
        store.immediate_begins = 0

        events = await asyncio.gather(
            *(
                store.append_grouped(sid, "test.event", {"index": index})
                for index, sid in enumerate(session_ids)
            )
        )

        assert store.immediate_begins == 1
        assert [event.seq for event in events] == [1] * len(session_ids)
        for sid in session_ids:
            assert store.verify_chain(sid) is True
            assert len(store.list_events(sid)) == 1

    asyncio.run(exercise())


def test_grouped_appends_preserve_same_session_hash_order(tmp_path):
    async def exercise():
        store = TracingGroupedStore(tmp_path / "same-session.db")
        store.create_session("shared")
        store.immediate_begins = 0

        events = await asyncio.gather(
            *(store.append_grouped("shared", "step", {"index": index}) for index in range(12))
        )

        assert store.immediate_begins == 1
        assert [event.seq for event in events] == list(range(1, 13))
        persisted = store.list_events("shared")
        assert [event.payload["index"] for event in persisted] == list(range(12))
        assert persisted[0].prev_hash == "GENESIS"
        for previous, current in zip(persisted, persisted[1:]):
            assert current.prev_hash == previous.hash
        assert store.verify_chain("shared") is True

    asyncio.run(exercise())


def test_failed_group_is_rolled_back_then_isolated_per_request(tmp_path):
    async def exercise():
        store = TracingGroupedStore(tmp_path / "isolation.db")
        store.create_session("good-a")
        store.create_session("good-b")
        store.immediate_begins = 0

        results = await asyncio.gather(
            store.append_grouped("good-a", "step", {"value": "a"}),
            store.append_grouped("missing", "step", {"value": "bad"}),
            store.append_grouped("good-b", "step", {"value": "b"}),
            return_exceptions=True,
        )

        assert results[0].session_id == "good-a"
        assert isinstance(results[1], KeyError)
        assert results[2].session_id == "good-b"
        assert [event.payload["value"] for event in store.list_events("good-a")] == ["a"]
        assert [event.payload["value"] for event in store.list_events("good-b")] == ["b"]
        assert store.verify_chain("good-a") is True
        assert store.verify_chain("good-b") is True
        # One failed shared transaction, followed by three isolated attempts.
        assert store.immediate_begins == 4

    asyncio.run(exercise())


def test_engine_groups_only_sinkless_builtin_emits(tmp_path):
    async def exercise():
        sinkless_store = SpyGroupedStore(tmp_path / "sinkless.db")
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
        assert sinkless_store.grouped_calls > 0

        streaming_store = SpyGroupedStore(tmp_path / "streaming.db")
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
        assert streaming_store.grouped_calls == 0
        assert streaming_store.sync_append_calls > 0
        assert seen[-1] == "run.completed"

    asyncio.run(exercise())


def test_event_store_plugin_without_group_api_keeps_legacy_path(tmp_path):
    async def exercise():
        store = EventStore(tmp_path / "plugin.db")
        engine = EcomEvoEngine(
            tmp_path / "plugin.db",
            plugin_overrides={"event.store": store},
        )
        summary = await engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint="merchant_review",
        )
        assert summary.event_chain_valid is True
        assert store.verify_chain(summary.session_id) is True

    asyncio.run(exercise())
