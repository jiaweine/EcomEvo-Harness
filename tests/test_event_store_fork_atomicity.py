from __future__ import annotations

import pytest

from ecomevo.runtime.event_store import EventStore


def _seed_source(store: EventStore, count: int = 8) -> None:
    store.create_session("source")
    for index in range(count):
        store.append("source", "source.event", {"index": index})


def test_fork_rolls_back_if_event_copy_fails(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "fork.db")
    _seed_source(store)

    original = EventStore._append_in_transaction
    copied = 0

    def fail_mid_copy(connection, session_id, event_type, payload, *, tail=None):
        nonlocal copied
        if session_id == "fork":
            copied += 1
            if copied == 4:
                raise RuntimeError("injected fork copy failure")
        return original(
            connection,
            session_id,
            event_type,
            payload,
            tail=tail,
        )

    monkeypatch.setattr(EventStore, "_append_in_transaction", staticmethod(fail_mid_copy))

    with pytest.raises(RuntimeError, match="injected fork copy failure"):
        store.fork("source", 8, "fork")

    assert not store.has_session("fork")


def test_fork_uses_one_writer_transaction(tmp_path):
    statements: list[str] = []

    class TracingEventStore(EventStore):
        def _conn(self):
            connection = super()._conn()
            connection.set_trace_callback(statements.append)
            return connection

    store = TracingEventStore(tmp_path / "fork-writes.db")
    _seed_source(store, count=32)
    statements.clear()

    store.fork("source", 32, "fork")

    begins = [statement for statement in statements if statement.lstrip().upper().startswith("BEGIN")]
    assert len(begins) == 1
    assert store.verify_chain("fork")
