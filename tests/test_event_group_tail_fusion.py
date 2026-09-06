from __future__ import annotations

from types import SimpleNamespace

import pytest

from ecomevo.runtime.bundled_event_store import BundledEventStore


def _request(session_id: str, event_type: str, payload: dict | None = None):
    return SimpleNamespace(
        session_id=session_id,
        event_type=event_type,
        payload=dict(payload or {}),
    )


class TracingBundledEventStore(BundledEventStore):
    def __init__(self, path):
        self.statements: list[str] = []
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()
        connection.set_trace_callback(self.statements.append)
        return connection


def test_append_group_tail_fusion_preserves_empty_and_repeated_session_chain(tmp_path):
    store = BundledEventStore(tmp_path / "events.db")
    store.create_session("empty")
    store.create_session("repeat")
    seed = store.append("repeat", "seed", {"n": 0})
    assert seed.seq == 1

    persisted = store._persist_append_group(
        [
            _request("empty", "first", {"n": 1}),
            _request("repeat", "second", {"n": 2}),
            _request("repeat", "third", {"n": 3}),
        ]
    )

    assert persisted[0].seq == 1
    assert persisted[0].prev_hash == "GENESIS"
    assert [event.seq for event in persisted[1:]] == [2, 3]
    assert persisted[2].prev_hash == persisted[1].hash
    assert store.verify_chain("empty")
    assert store.verify_chain("repeat")


def test_append_group_tail_fusion_rolls_back_when_any_session_is_unknown(tmp_path):
    store = BundledEventStore(tmp_path / "events.db")
    store.create_session("known")

    with pytest.raises(KeyError, match="unknown session: missing"):
        store._persist_append_group(
            [
                _request("known", "would-have-written", {"ok": True}),
                _request("missing", "invalid", {}),
            ]
        )

    with store._conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM events WHERE session_id=?",
            ("known",),
        ).fetchone()["n"]
    assert count == 0


def test_append_group_tail_fusion_uses_one_set_lookup_for_bounded_batch(tmp_path):
    store = TracingBundledEventStore(tmp_path / "events.db")
    session_ids = [f"s-{index:02d}" for index in range(64)]
    for session_id in session_ids:
        store.create_session(session_id)
        store.append(session_id, "seed", {"session": session_id})

    store.statements.clear()
    persisted = store._persist_append_group(
        [_request(session_id, "next", {"session": session_id}) for session_id in session_ids]
    )

    statements = [statement.upper() for statement in store.statements]
    set_lookups = [
        statement
        for statement in statements
        if statement.lstrip().startswith("WITH WANTED(SESSION_ID) AS (VALUES")
    ]
    legacy_tail_lookups = [
        statement
        for statement in statements
        if "ORDER BY E.SEQ DESC LIMIT 1" in statement
    ]
    begin_immediate = [
        statement for statement in statements if statement.strip() == "BEGIN IMMEDIATE"
    ]

    assert len(persisted) == 64
    assert all(event.seq == 2 for event in persisted)
    assert len(set_lookups) == 1
    assert legacy_tail_lookups == []
    assert len(begin_immediate) == 1
    assert all(store.verify_chain(session_id) for session_id in session_ids)


def test_append_group_tail_helper_chunks_direct_oversized_call(tmp_path):
    store = TracingBundledEventStore(tmp_path / "events.db")
    session_ids = [f"oversized-{index:03d}" for index in range(65)]
    for session_id in session_ids:
        store.create_session(session_id)

    store.statements.clear()
    with store._conn() as connection:
        tails = store._session_tails_for_append_group(connection, session_ids)

    set_lookups = [
        statement
        for statement in store.statements
        if statement.upper().lstrip().startswith("WITH WANTED(SESSION_ID) AS (VALUES")
    ]
    assert set(tails) == set(session_ids)
    assert all(tail == {"seq": None, "hash": None} for tail in tails.values())
    assert len(set_lookups) == 2
