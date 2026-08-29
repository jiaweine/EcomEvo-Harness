from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from ecomevo.runtime.event_store import EventStore
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer
from ecomevo.runtime.skills import AdaptiveSkillLibrary


class CountingEventStore(EventStore):
    def __init__(self, path):
        self.connection_count = 0
        super().__init__(path)

    def _conn(self):
        self.connection_count += 1
        return super()._conn()


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


def test_event_store_hot_reads_use_single_connection(tmp_path):
    store = CountingEventStore(tmp_path / "events.db")
    store.create_session("s1", {"domain": "merchant_review"})
    first = store.append("s1", "run.started", {"goal": "review"})
    store.append("s1", "plan.created", {"tools": ["merchant.inspect"]})

    store.connection_count = 0
    checkpoint = store.save_checkpoint("s1", {"phase": "planned"})
    assert store.connection_count == 1
    assert checkpoint["seq"] == 2
    assert checkpoint["event_hash"] != "GENESIS"

    store.connection_count = 0
    assert store.verify_chain("s1") is True
    assert store.connection_count == 1

    store.connection_count = 0
    sessions = store.list_sessions()
    assert store.connection_count == 1
    assert sessions[0]["event_count"] == 2
    assert sessions[0]["hash_chain_valid"] is True

    restored = store.restore_checkpoint("s1")
    assert restored is not None
    assert restored["phase"] == "planned"
    assert restored["_checkpoint"]["event_hash"] == checkpoint["event_hash"]
    assert first.seq == 1


def test_create_session_and_first_event_share_one_atomic_write(tmp_path):
    store = EventStore(tmp_path / "events.db")
    circular = {}
    circular["self"] = circular

    with pytest.raises(ValueError):
        store.create_session_and_append(
            "broken",
            "goal.parsed",
            circular,
            meta={"domain": "merchant_review"},
        )
    assert store.has_session("broken") is False

    event = store.create_session_and_append(
        "s1",
        "goal.parsed",
        {"goal": "review"},
        meta={"domain": "merchant_review"},
    )
    assert event.seq == 1
    assert event.prev_hash == "GENESIS"
    assert store.verify_chain("s1") is True
    sessions = store.list_sessions(limit=1)
    assert sessions[0]["event_count"] == 1


def test_checkpoint_and_audit_event_commit_or_rollback_together(tmp_path):
    store = EventStore(tmp_path / "events.db")
    first = store.create_session_and_append("s1", "goal.parsed", {"goal": "review"})

    reference, audit = store.save_checkpoint_and_append(
        "s1",
        {"stage": "before-plan", "belief": {"confidence": 0.2}},
        "runtime.checkpointed",
        {"stage": "before-plan"},
    )
    assert reference["seq"] == 1
    assert reference["event_hash"] == first.hash
    assert audit.seq == 2
    assert audit.prev_hash == first.hash
    assert audit.payload["seq"] == 1
    assert audit.payload["state_hash"] == reference["state_hash"]
    restored = store.restore_checkpoint("s1", seq=1)
    assert restored is not None
    assert restored["stage"] == "before-plan"
    assert store.verify_chain("s1") is True

    store2 = EventStore(tmp_path / "rollback.db")
    store2.create_session_and_append("s2", "goal.parsed", {"goal": "review"})
    circular = {}
    circular["self"] = circular
    with pytest.raises(ValueError):
        store2.save_checkpoint_and_append(
            "s2",
            {"stage": "must-rollback"},
            "runtime.checkpointed",
            {"bad": circular},
        )
    assert store2.restore_checkpoint("s2") is None
    assert len(store2.list_events("s2")) == 1
    assert store2.verify_chain("s2") is True


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
