from __future__ import annotations

import threading
import time
from pathlib import Path

from ecomevo.models import EvolutionPatch
from ecomevo.runtime.bundled_event_store import BundledEventStore


def _patch(*, patch_id: str, value: str = "inspect") -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id,
        created_at=time.time(),
        reason="positive-cache-test",
        target="tool",
        patch={"preferred_tools": [value]},
        replay_cases=1,
        regression_before=0.5,
        regression_after=0.4,
        accepted=True,
    )


def _is_patch_lookup(statement: str) -> bool:
    normalized = " ".join(statement.strip().upper().split())
    return normalized.startswith(
        "SELECT PAYLOAD_JSON FROM EVOLUTION_PATCHES WHERE FINGERPRINT="
    )


class SelectCountingBundledEventStore(BundledEventStore):
    def __init__(self, path: Path):
        self._select_lock = threading.RLock()
        self.patch_lookup_selects = 0
        super().__init__(path)
        self.reset_selects()

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if _is_patch_lookup(statement):
                with self._select_lock:
                    self.patch_lookup_selects += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_selects(self) -> None:
        with self._select_lock:
            self.patch_lookup_selects = 0


def test_novel_insert_seeds_cache_and_hits_return_fresh_payload(tmp_path: Path) -> None:
    store = SelectCountingBundledEventStore(tmp_path / "seeded.db")
    first = _patch(patch_id="original")
    assert store.save_patch_if_novel(first) is None

    store.reset_selects()
    duplicate = store.save_patch_if_novel(_patch(patch_id="duplicate"))
    assert duplicate is not None
    assert duplicate["patch_id"] == first.patch_id
    assert store.patch_lookup_selects == 0

    duplicate["patch"]["preferred_tools"].append("poison")
    duplicate_again = store.save_patch_if_novel(_patch(patch_id="duplicate-again"))
    assert duplicate_again is not None
    assert duplicate_again["patch_id"] == first.patch_id
    assert duplicate_again["patch"] == first.patch
    assert store.patch_lookup_selects == 0


def test_sqlite_hit_populates_cache_for_store_started_after_seed(tmp_path: Path) -> None:
    db = tmp_path / "restart.db"
    seed_store = BundledEventStore(db)
    first = _patch(patch_id="persisted")
    assert seed_store.save_patch_if_novel(first) is None

    store = SelectCountingBundledEventStore(db)
    store.reset_selects()
    one = store.save_patch_if_novel(_patch(patch_id="duplicate-one"))
    assert one is not None and one["patch_id"] == first.patch_id
    assert store.patch_lookup_selects == 1

    two = store.save_patch_if_novel(_patch(patch_id="duplicate-two"))
    assert two is not None and two["patch_id"] == first.patch_id
    assert store.patch_lookup_selects == 1


def test_no_negative_cache_hides_patch_added_by_second_store(tmp_path: Path) -> None:
    db = tmp_path / "cross-instance.db"
    old_store = SelectCountingBundledEventStore(db)
    old_store.reset_selects()

    external = BundledEventStore(db)
    external_patch = _patch(patch_id="external", value="later")
    assert external.save_patch_if_novel(external_patch) is None

    one = old_store.save_patch_if_novel(
        _patch(patch_id="external-duplicate-one", value="later")
    )
    assert one is not None and one["patch_id"] == external_patch.patch_id
    assert old_store.patch_lookup_selects == 1

    two = old_store.save_patch_if_novel(
        _patch(patch_id="external-duplicate-two", value="later")
    )
    assert two is not None and two["patch_id"] == external_patch.patch_id
    assert old_store.patch_lookup_selects == 1


def test_positive_cache_is_bounded_lru(tmp_path: Path) -> None:
    store = BundledEventStore(tmp_path / "bounded.db")
    for index in range(257):
        store._remember_patch_payload(
            f"fingerprint-{index}",
            {"patch_id": f"patch-{index}", "patch": {"index": index}},
        )

    assert len(store._patch_positive_cache) == 256
    assert store._cached_patch_payload("fingerprint-0") is None
    latest = store._cached_patch_payload("fingerprint-256")
    assert latest is not None
    assert latest["patch_id"] == "patch-256"

    latest["patch"]["index"] = -1
    assert store._cached_patch_payload("fingerprint-256")["patch"]["index"] == 256
