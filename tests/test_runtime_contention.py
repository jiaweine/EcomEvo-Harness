from __future__ import annotations

import sqlite3
import threading

import pytest
from concurrent.futures import ThreadPoolExecutor

from ecomevo.runtime.event_store import EventStore
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer
from ecomevo.runtime.skills import AdaptiveSkillLibrary


class CountingEventStore(EventStore):
    def __init__(self, path):
        self.connection_count = 0
        super().__init__(path)

    def _open_conn(self):
        self.connection_count += 1
        return super()._open_conn()


def test_initialized_skill_policy_read_does_not_wait_for_sqlite_writer(tmp_path):
    db = tmp_path / "skills.db"
    library = AdaptiveSkillLibrary(db)
    expected = library.policy("merchant_review")

    writer = sqlite3.connect(db, timeout=30)
    writer.execute("PRAGMA busy_timeout=30000")
    writer.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(library.policy, "merchant_review")
            observed = future.result(timeout=0.5)
    finally:
        writer.rollback()
        writer.close()

    assert observed == expected


def test_initialized_harness_profile_and_snapshot_do_not_wait_for_writer(tmp_path):
    db = tmp_path / "harness.db"
    optimizer = HarnessEvolutionOptimizer(db)
    expected_profile = optimizer.profile("merchant_review", session_key="stable-session")
    expected_snapshot = optimizer.snapshot("merchant_review")
    assert len(expected_profile["component_ids"]) == len(optimizer.KINDS)

    writer = sqlite3.connect(db, timeout=30)
    writer.execute("PRAGMA busy_timeout=30000")
    writer.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            profile_future = pool.submit(
                optimizer.profile, "merchant_review", session_key="stable-session"
            )
            snapshot_future = pool.submit(optimizer.snapshot, "merchant_review")
            observed_profile = profile_future.result(timeout=0.5)
            observed_snapshot = snapshot_future.result(timeout=0.5)
    finally:
        writer.rollback()
        writer.close()

    assert observed_profile == expected_profile
    assert observed_snapshot == expected_snapshot


def test_event_store_reuses_one_connection_for_same_thread_hot_path(tmp_path):
    store = CountingEventStore(tmp_path / "events.db")
    assert store.connection_count == 1
    store.create_session("s1", {"domain": "merchant_review"})
    first = store.append("s1", "run.started", {"goal": "review"})
    store.append("s1", "plan.created", {"tools": ["merchant.inspect"]})
    checkpoint = store.save_checkpoint("s1", {"phase": "planned"})
    assert store.verify_chain("s1") is True
    sessions = store.list_sessions()
    restored = store.restore_checkpoint("s1")

    # All operations above run on the main engine thread and must reuse the connection
    # opened during store initialization rather than reconnecting per event/read.
    assert store.connection_count == 1
    assert checkpoint["seq"] == 2
    assert checkpoint["event_hash"] != "GENESIS"
    assert sessions[0]["event_count"] == 2
    assert sessions[0]["hash_chain_valid"] is True
    assert restored is not None
    assert restored["phase"] == "planned"
    assert restored["_checkpoint"]["event_hash"] == checkpoint["event_hash"]
    assert first.seq == 1


def test_event_store_uses_independent_reused_connection_per_thread(tmp_path):
    store = CountingEventStore(tmp_path / "events.db")
    workers = 4
    barrier = threading.Barrier(workers)

    def connection_identity(_index: int):
        first = store._conn()
        barrier.wait(timeout=3)
        second = store._conn()
        assert second is first
        return id(first)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        identities = list(pool.map(connection_identity, range(workers)))

    assert len(set(identities)) == workers
    # One main-thread initialization handle + exactly one handle for each worker.
    assert store.connection_count == 1 + workers


def test_event_store_rebuilds_inherited_connection_when_pid_changes(tmp_path):
    store = CountingEventStore(tmp_path / "events.db")
    original = store._conn()
    store._local.pid = -1  # simulate a forked child observing a parent-owned handle
    replacement = store._conn()

    assert replacement is not original
    assert store.connection_count == 2
    with pytest.raises(sqlite3.ProgrammingError):
        original.execute("SELECT 1")


def test_event_store_reused_connection_recovers_after_transaction_error(tmp_path):
    store = CountingEventStore(tmp_path / "events.db")
    store.create_session("valid")
    with pytest.raises(KeyError):
        store.append("missing", "bad", {"value": 0})

    event = store.append("valid", "good", {"value": 1})
    assert event.seq == 1
    assert store.verify_chain("valid") is True
    assert store.connection_count == 1


def test_event_store_multi_instance_appends_preserve_one_hash_chain(tmp_path):
    db = tmp_path / "events.db"
    owner = EventStore(db)
    owner.create_session("shared")
    stores = [EventStore(db) for _ in range(8)]

    def append(index: int):
        return stores[index % len(stores)].append("shared", "stress.event", {"index": index})

    with ThreadPoolExecutor(max_workers=16) as pool:
        events = list(pool.map(append, range(128)))

    seqs = sorted(event.seq for event in events)
    assert seqs == list(range(1, 129))
    persisted = owner.list_events("shared")
    assert [event.seq for event in persisted] == list(range(1, 129))
    assert owner.verify_chain("shared") is True


def test_checkpoint_explicit_seq_remains_bound_to_original_event_hash(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.create_session("s1")
    first = store.append("s1", "one", {"value": 1})
    store.append("s1", "two", {"value": 2})

    checkpoint = store.save_checkpoint("s1", {"phase": "after-one"}, seq=1)
    assert checkpoint["event_hash"] == first.hash
    restored = store.restore_checkpoint("s1", seq=1)
    assert restored is not None
    assert restored["phase"] == "after-one"
    assert restored["_checkpoint"]["event_hash"] == first.hash
