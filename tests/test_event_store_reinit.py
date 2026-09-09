from __future__ import annotations

import sqlite3

from ecomevo.models import EvolutionPatch
from ecomevo.runtime.event_store import EventStore


class TracingEventStore(EventStore):
    def __init__(self, path):
        self.sql: list[str] = []
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()
        connection.set_trace_callback(self.sql.append)
        return connection


def _patch(index: int, *, patch_id: str | None = None) -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id or f"patch-{index}",
        created_at=float(index + 1),
        reason="reinit pressure fixture",
        target="planner",
        patch={"weight": index},
        replay_cases=8,
        regression_before=0.5,
        regression_after=0.6,
        accepted=True,
    )


def _fingerprint_updates(store: TracingEventStore) -> list[str]:
    return [
        statement
        for statement in store.sql
        if statement.lstrip().upper().startswith("UPDATE EVOLUTION_PATCHES SET FINGERPRINT")
    ]


def test_clean_reopen_does_not_rewrite_evolution_patch_fingerprints(tmp_path):
    path = tmp_path / "reopen.db"
    store = EventStore(path)
    for index in range(128):
        assert store.save_patch_if_novel(_patch(index)) is None

    assert _fingerprint_updates(TracingEventStore(path)) == []


def test_reopen_repairs_newer_legacy_null_fingerprint_once(tmp_path):
    path = tmp_path / "legacy-null.db"
    store = EventStore(path)
    original = _patch(1)
    assert store.save_patch_if_novel(original) is None

    duplicate = _patch(1, patch_id="legacy-duplicate")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO evolution_patches(patch_id,created_at,payload_json,fingerprint) "
            "VALUES(?,?,?,NULL)",
            (duplicate.patch_id, duplicate.created_at + 1, duplicate.model_dump_json()),
        )

    EventStore(path)

    fingerprint = EventStore._patch_fingerprint(original)
    with sqlite3.connect(path) as connection:
        rows = dict(
            connection.execute(
                "SELECT patch_id,fingerprint FROM evolution_patches ORDER BY created_at DESC"
            ).fetchall()
        )

    assert rows[duplicate.patch_id] == fingerprint
    assert rows[original.patch_id] is None
    assert _fingerprint_updates(TracingEventStore(path)) == []
