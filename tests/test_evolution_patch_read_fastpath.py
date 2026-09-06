from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ecomevo.models import EvolutionPatch
from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.event_store import EventStore


def _patch(*, patch_id: str, value: str = "inspect") -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id,
        created_at=time.time(),
        reason="test",
        target="tool",
        patch={"preferred_tools": [value]},
        replay_cases=1,
        regression_before=0.5,
        regression_after=0.4,
        accepted=True,
    )


class _BeginCountingMixin:
    def __init__(self, path: Path):
        self._begin_lock = threading.Lock()
        self.immediate_begins = 0
        super().__init__(path)
        self.reset_begins()

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._begin_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_begins(self) -> None:
        with self._begin_lock:
            self.immediate_begins = 0


class CountingEventStore(_BeginCountingMixin, EventStore):
    pass


class CountingBundledEventStore(_BeginCountingMixin, BundledEventStore):
    pass


def test_duplicate_patch_skips_writer_only_for_bundled_store(tmp_path: Path) -> None:
    base = CountingEventStore(tmp_path / "base.db")
    bundled = CountingBundledEventStore(tmp_path / "bundled.db")

    for store in (base, bundled):
        first = _patch(patch_id="first")
        duplicate = _patch(patch_id="second")
        assert store.save_patch_if_novel(first) is None
        store.reset_begins()
        existing = store.save_patch_if_novel(duplicate)
        assert existing is not None
        assert existing["patch_id"] == "first"

    assert base.immediate_begins == 1
    assert bundled.immediate_begins == 0


def test_patch_id_collision_still_raises_integrity_error(tmp_path: Path) -> None:
    store = CountingBundledEventStore(tmp_path / "collision.db")
    assert store.save_patch_if_novel(_patch(patch_id="same", value="one")) is None

    store.reset_begins()
    with pytest.raises(sqlite3.IntegrityError):
        store.save_patch_if_novel(_patch(patch_id="same", value="two"))

    assert store.immediate_begins == 1


def test_concurrent_first_observation_inserts_one_semantic_patch(tmp_path: Path) -> None:
    store = CountingBundledEventStore(tmp_path / "concurrent.db")
    patches = [_patch(patch_id=f"patch-{index}") for index in range(16)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(store.save_patch_if_novel, patches))

    assert sum(result is None for result in results) == 1
    stored = store.list_patches(20)
    assert len(stored) == 1
    stored_id = stored[0]["patch_id"]
    assert all(result is None or result["patch_id"] == stored_id for result in results)


def test_duplicate_payload_is_original_stored_patch(tmp_path: Path) -> None:
    store = BundledEventStore(tmp_path / "payload.db")
    first = _patch(patch_id="original")
    duplicate = _patch(patch_id="duplicate")

    assert store.save_patch_if_novel(first) is None
    existing = store.save_patch_if_novel(duplicate)

    assert existing is not None
    assert existing["patch_id"] == "original"
    assert existing["patch"] == first.patch
