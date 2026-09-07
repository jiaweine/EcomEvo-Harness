from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import statistics
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import writer_profile_gate as writer_profile
from ecomevo.models import EvolutionPatch
from ecomevo.runtime.bundled_event_store import BundledEventStore
from ecomevo.runtime.event_store import EventStore


DUPLICATE_CALLS = 512
RUNTIME_TASKS = 32
DIRECT_EXPERIMENTS = 5
RUNTIME_EXPERIMENTS = 3
CACHE_LIMIT = 256
WALL_RATIO_LIMIT = 0.35


def patch(*, patch_id: str, value: str = "inspect") -> EvolutionPatch:
    return EvolutionPatch(
        patch_id=patch_id,
        created_at=time.time(),
        reason="positive-cache-probe",
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


class CountingProfiledEventStore(writer_profile.ProfiledEventStore):
    """Current #74 bundled path plus exact fingerprint-lookup accounting."""

    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._patch_trace_lock = threading.RLock()
        self.patch_lookup_selects = 0
        super().__init__(path, profile)

    def _conn(self):
        # Recreate ProfiledEventStore's trace callback so the same connection can also
        # count the exact evolution-patch fingerprint SELECT used by #74.
        connection = EventStore._conn(self)
        connection_id = id(connection)

        def trace(statement: str) -> None:
            self._writer_profile.trace(connection_id, statement)
            if _is_patch_lookup(statement):
                with self._patch_trace_lock:
                    self.patch_lookup_selects += 1

        connection.set_trace_callback(trace)
        return connection

    def reset_patch_metrics(self) -> None:
        with self._patch_trace_lock:
            self.patch_lookup_selects = 0


class CachedProfiledEventStore(CountingProfiledEventStore):
    """Diagnostic positive-only cache layered on the unchanged #74 persistence path."""

    def __init__(self, path: Path, profile: writer_profile.WriterProfile):
        self._patch_cache_lock = threading.RLock()
        self._patch_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0
        super().__init__(path, profile)

    def reset_patch_metrics(self) -> None:
        super().reset_patch_metrics()
        with self._patch_cache_lock:
            self.cache_hits = 0
            self.cache_misses = 0

    def _cache_get(self, fingerprint: str) -> dict[str, Any] | None:
        with self._patch_cache_lock:
            payload = self._patch_cache.get(fingerprint)
            if payload is None:
                self.cache_misses += 1
                return None
            self._patch_cache.move_to_end(fingerprint)
            self.cache_hits += 1
            # #74 reparses payload_json for every duplicate lookup, so callers receive
            # a fresh mutable object. Preserve that observable behavior on cache hits.
            return copy.deepcopy(payload)

    def _cache_put(self, fingerprint: str, payload: dict[str, Any]) -> None:
        with self._patch_cache_lock:
            self._patch_cache[fingerprint] = copy.deepcopy(payload)
            self._patch_cache.move_to_end(fingerprint)
            while len(self._patch_cache) > CACHE_LIMIT:
                self._patch_cache.popitem(last=False)

    def save_patch_if_novel(self, patch_value: EvolutionPatch) -> dict[str, Any] | None:
        def operation() -> dict[str, Any] | None:
            fingerprint = self._patch_fingerprint(patch_value)
            cached = self._cache_get(fingerprint)
            if cached is not None:
                return cached

            # Keep #74's WAL-read hit, writer-locked recheck, collision behavior and
            # INSERT path unchanged. Only confirmed positives are admitted to cache.
            existing = BundledEventStore.save_patch_if_novel(self, patch_value)
            if existing is None:
                self._cache_put(
                    fingerprint,
                    json.loads(patch_value.model_dump_json()),
                )
            else:
                self._cache_put(fingerprint, existing)
            return existing

        return writer_profile._timed(
            self._writer_profile,
            "event.evolution_patch",
            operation,
        )


def _stage(report: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in report["stages"] if row["stage"] == name), None)


def semantic_probe(root: Path) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    db = root / "semantic.db"
    store = CachedProfiledEventStore(db, profile)

    seed = patch(patch_id="seed")
    expected = json.loads(seed.model_dump_json())
    assert store.save_patch_if_novel(seed) is None

    duplicate = store.save_patch_if_novel(patch(patch_id="duplicate"))
    assert duplicate == expected
    duplicate["patch"]["preferred_tools"].append("poison")
    duplicate_again = store.save_patch_if_novel(patch(patch_id="duplicate-again"))
    mutation_isolated = duplicate_again == expected

    collision_error = None
    try:
        store.save_patch_if_novel(patch(patch_id=seed.patch_id, value="different"))
    except sqlite3.IntegrityError as exc:
        collision_error = type(exc).__name__

    # Never negative-cache a miss. A second store can append a new fingerprint after
    # this instance was created; the old instance must discover it on its next miss.
    external = BundledEventStore(db)
    external_seed = patch(patch_id="external-seed", value="external")
    assert external.save_patch_if_novel(external_seed) is None
    expected_external = json.loads(external_seed.model_dump_json())

    before_external = store.patch_lookup_selects
    discovered = store.save_patch_if_novel(
        patch(patch_id="external-duplicate", value="external")
    )
    after_external = store.patch_lookup_selects
    discovered_again = store.save_patch_if_novel(
        patch(patch_id="external-duplicate-again", value="external")
    )
    after_external_again = store.patch_lookup_selects

    return {
        "duplicate_returns_original": duplicate_again == expected,
        "caller_mutation_isolated": mutation_isolated,
        "patch_id_collision_error": collision_error,
        "cross_instance_discovery_equal": discovered == expected_external,
        "cross_instance_cached_equal": discovered_again == expected_external,
        "cross_instance_first_lookup_selects": after_external - before_external,
        "cross_instance_second_lookup_selects": after_external_again - after_external,
        "cache_entries": len(store._patch_cache),
        "cache_limit": CACHE_LIMIT,
    }


def direct_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    store_type = (
        CountingProfiledEventStore if mode == "baseline" else CachedProfiledEventStore
    )
    store = store_type(root / f"direct-{experiment}-{mode}.db", profile)
    seed = patch(patch_id=f"{mode}-{experiment}-seed")
    assert store.save_patch_if_novel(seed) is None

    profile.reset()
    store.reset_patch_metrics()
    started = time.perf_counter()
    for index in range(DUPLICATE_CALLS):
        existing = store.save_patch_if_novel(
            patch(patch_id=f"{mode}-{experiment}-duplicate-{index}")
        )
        if existing is None or existing["patch_id"] != seed.patch_id:
            raise AssertionError("duplicate lookup stopped returning the original patch")
    wall = time.perf_counter() - started
    report = profile.report(DUPLICATE_CALLS)
    stage = _stage(report, "event.evolution_patch")
    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": wall,
        "fingerprint_selects": store.patch_lookup_selects,
        "cache_hits": int(getattr(store, "cache_hits", 0)),
        "cache_misses": int(getattr(store, "cache_misses", 0)),
        "writer_transactions": int(stage["transactions"]) if stage else 0,
        "operation_ms_total": float(stage["operation_ms_total"]) if stage else 0.0,
        "unattributed_transactions": int(report["unattributed_transactions"]),
    }


def build_engine(
    db: Path,
    profile: writer_profile.WriterProfile,
    mode: str,
):
    sandbox = writer_profile.ActionSandbox()
    event_store = (
        CountingProfiledEventStore(db, profile)
        if mode == "baseline"
        else CachedProfiledEventStore(db, profile)
    )
    skills = writer_profile.ProfiledSkills(db, profile)
    harness = writer_profile.ProfiledHarness(db, profile, sandbox=sandbox)
    engine = writer_profile.EcomEvoEngine(
        db,
        plugin_overrides={
            "event.store": event_store,
            "memory.skills": skills,
            "evolver.harness": harness,
            "sandbox.action": sandbox,
        },
    )
    engine.autonomy.policy.routing = writer_profile.ProfiledRouting(db, profile)
    return engine, event_store


async def runtime_probe(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    profile = writer_profile.WriterProfile()
    engine, event_store = build_engine(
        root / f"runtime-{experiment}-{mode}.db",
        profile,
        mode,
    )
    warm = await writer_profile._run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    profile.reset()
    event_store.reset_patch_metrics()
    started = time.perf_counter()
    summaries = await writer_profile._run_batch(engine, RUNTIME_TASKS)
    wall = time.perf_counter() - started
    report = profile.report(RUNTIME_TASKS)
    stage = _stage(report, "event.evolution_patch")
    failures: list[str] = []
    if any(not summary.event_chain_valid for summary in summaries):
        failures.append("runtime produced invalid event chain")
    if report["unattributed_transactions"]:
        failures.append("runtime lost writer attribution")

    return {
        "mode": mode,
        "experiment": experiment,
        "wall_seconds": wall,
        "fingerprint_selects": event_store.patch_lookup_selects,
        "cache_hits": int(getattr(event_store, "cache_hits", 0)),
        "cache_misses": int(getattr(event_store, "cache_misses", 0)),
        "evolution_patch_transactions": int(stage["transactions"]) if stage else 0,
        "evolution_patch_operation_ms_total": (
            float(stage["operation_ms_total"]) if stage else 0.0
        ),
        "total_writer_transactions": int(report["total_transactions"]),
        "failures": failures,
    }


def _median(rows: list[dict[str, Any]], key: str) -> float:
    return float(statistics.median(float(row[key]) for row in rows))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    direct: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    runtime: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}

    with tempfile.TemporaryDirectory(prefix="ecomevo-patch-positive-cache-") as tmp:
        root = Path(tmp)
        semantics = semantic_probe(root)
        expected_semantics = {
            "duplicate_returns_original": True,
            "caller_mutation_isolated": True,
            "patch_id_collision_error": "IntegrityError",
            "cross_instance_discovery_equal": True,
            "cross_instance_cached_equal": True,
            "cross_instance_first_lookup_selects": 1,
            "cross_instance_second_lookup_selects": 0,
        }
        for key, expected in expected_semantics.items():
            if semantics.get(key) != expected:
                failures.append(
                    f"semantic {key} changed: {semantics.get(key)!r} != {expected!r}"
                )

        for experiment in range(DIRECT_EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            for mode in order:
                direct[mode].append(direct_probe(root, mode, experiment))

        for experiment in range(RUNTIME_EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            for mode in order:
                row = await runtime_probe(root, mode, experiment)
                runtime[mode].append(row)
                failures.extend(
                    f"runtime {mode}[{experiment}]: {failure}"
                    for failure in row["failures"]
                )

    wall_ratios: list[float] = []
    for experiment in range(DIRECT_EXPERIMENTS):
        baseline = direct["baseline"][experiment]
        candidate = direct["candidate"][experiment]
        ratio = candidate["wall_seconds"] / max(1e-9, baseline["wall_seconds"])
        wall_ratios.append(ratio)
        if baseline["fingerprint_selects"] != DUPLICATE_CALLS:
            failures.append(
                f"direct baseline[{experiment}] SELECT count "
                f"{baseline['fingerprint_selects']} != {DUPLICATE_CALLS}"
            )
        if candidate["fingerprint_selects"] != 0:
            failures.append(
                f"direct candidate[{experiment}] still queried SQLite "
                f"{candidate['fingerprint_selects']} times"
            )
        if candidate["cache_hits"] != DUPLICATE_CALLS:
            failures.append(
                f"direct candidate[{experiment}] cache hits "
                f"{candidate['cache_hits']} != {DUPLICATE_CALLS}"
            )
        if baseline["writer_transactions"] or candidate["writer_transactions"]:
            failures.append(f"direct experiment {experiment} reintroduced patch writers")
        if baseline["unattributed_transactions"] or candidate["unattributed_transactions"]:
            failures.append(f"direct experiment {experiment} lost writer attribution")

    median_wall_ratio = float(statistics.median(wall_ratios))
    if median_wall_ratio > WALL_RATIO_LIMIT:
        failures.append(
            f"direct median wall ratio {median_wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
        )

    for experiment in range(RUNTIME_EXPERIMENTS):
        baseline = runtime["baseline"][experiment]
        candidate = runtime["candidate"][experiment]
        if baseline["fingerprint_selects"] != RUNTIME_TASKS:
            failures.append(
                f"runtime baseline[{experiment}] SELECT count "
                f"{baseline['fingerprint_selects']} != {RUNTIME_TASKS}"
            )
        if candidate["fingerprint_selects"] != 0:
            failures.append(
                f"runtime candidate[{experiment}] still queried SQLite "
                f"{candidate['fingerprint_selects']} times"
            )
        if baseline["evolution_patch_transactions"] != 0:
            failures.append(f"runtime baseline[{experiment}] unexpectedly used patch writer")
        if candidate["evolution_patch_transactions"] != 0:
            failures.append(f"runtime candidate[{experiment}] unexpectedly used patch writer")

    baseline_runtime_wall = _median(runtime["baseline"], "wall_seconds")
    candidate_runtime_wall = _median(runtime["candidate"], "wall_seconds")
    baseline_runtime_op = _median(runtime["baseline"], "evolution_patch_operation_ms_total")
    candidate_runtime_op = _median(runtime["candidate"], "evolution_patch_operation_ms_total")

    return {
        "ok": not failures,
        "cache_limit": CACHE_LIMIT,
        "duplicate_calls": DUPLICATE_CALLS,
        "direct_experiments": DIRECT_EXPERIMENTS,
        "runtime_tasks": RUNTIME_TASKS,
        "runtime_experiments": RUNTIME_EXPERIMENTS,
        "wall_ratio_limit": WALL_RATIO_LIMIT,
        "semantics": semantics,
        "direct": direct,
        "direct_wall_ratios": [round(value, 4) for value in wall_ratios],
        "median_direct_wall_ratio": round(median_wall_ratio, 4),
        "runtime": runtime,
        "runtime_comparison": {
            "median_wall_ratio": round(
                candidate_runtime_wall / max(1e-9, baseline_runtime_wall), 4
            ),
            "median_patch_operation_ratio": round(
                candidate_runtime_op / max(0.001, baseline_runtime_op), 4
            ),
            "baseline_median_patch_selects": _median(
                runtime["baseline"], "fingerprint_selects"
            ),
            "candidate_median_patch_selects": _median(
                runtime["candidate"], "fingerprint_selects"
            ),
        },
        "failures": failures,
    }


def main() -> int:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
