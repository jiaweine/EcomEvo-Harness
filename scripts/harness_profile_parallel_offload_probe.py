from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer


CALLS = 256
WARMUP_CALLS = 32
EXPERIMENTS = 5
COLD_CALLS = 32
HEARTBEAT_SECONDS = 0.001
MAX_LAG_RATIO_LIMIT = 0.35
WALL_RATIO_LIMIT = 1.60
DOMAIN = "merchant_review"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def _profile_parallel(harness, session_key: str):
    task = asyncio.create_task(
        asyncio.to_thread(harness.profile, DOMAIN, session_key=session_key)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # A cold profile can bootstrap Harness components. Never expose cancellation
        # before the underlying read/bootstrap operation has reached a known outcome.
        try:
            await asyncio.shield(task)
        except Exception:
            raise
        raise cancelled


async def _heartbeat(stop: asyncio.Event, samples_ms: list[float]) -> None:
    loop = asyncio.get_running_loop()
    target = loop.time() + HEARTBEAT_SECONDS
    while not stop.is_set():
        await asyncio.sleep(max(0.0, target - loop.time()))
        now = loop.time()
        samples_ms.append(max(0.0, now - target) * 1000.0)
        target += HEARTBEAT_SECONDS


class TracingHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.profile_threads: set[int] = set()
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def profile(self, *args, **kwargs):
        with self._trace_lock:
            self.profile_threads.add(threading.get_ident())
        return super().profile(*args, **kwargs)


async def _run_arm(
    harness: TracingHarness,
    mode: str,
    session_keys: list[str],
    *,
    main_thread_id: int,
) -> dict[str, Any]:
    harness.profile_threads.clear()
    heartbeat_samples: list[float] = []
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(stop, heartbeat_samples))

    async def one(session_key: str):
        if mode == "baseline":
            return harness.profile(DOMAIN, session_key=session_key)
        return await _profile_parallel(harness, session_key)

    await asyncio.sleep(0)
    started = time.perf_counter()
    outputs = await asyncio.gather(*(one(key) for key in session_keys))
    elapsed = time.perf_counter() - started
    stop.set()
    await asyncio.sleep(HEARTBEAT_SECONDS * 2)
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

    thread_ids = sorted(harness.profile_threads)
    return {
        "wall_seconds": elapsed,
        "outputs": outputs,
        "heartbeat_p99_ms": _percentile(heartbeat_samples, 0.99),
        "heartbeat_max_ms": max(heartbeat_samples, default=0.0),
        "thread_ids": thread_ids,
        "ran_on_main_thread": main_thread_id in thread_ids,
    }


async def _cold_semantics(path: Path) -> dict[str, Any]:
    harness = BundledHarnessEvolutionOptimizer(path)
    outputs = await asyncio.gather(
        *(_profile_parallel(harness, f"cold-{index}") for index in range(COLD_CALLS))
    )
    component_sets = [tuple(output.get("component_ids") or []) for output in outputs]
    snapshot = harness.snapshot(DOMAIN)
    active = [row for row in snapshot.get("components") or [] if row.get("status") == "active"]
    return {
        "outputs": outputs,
        "unique_component_sets": len(set(component_sets)),
        "active_components": len(active),
        "active_kinds": sorted(str(row.get("kind")) for row in active),
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    main_thread_id = threading.get_ident()
    with tempfile.TemporaryDirectory(prefix="ecomevo-harness-profile-parallel-") as tmp:
        cold = await _cold_semantics(Path(tmp) / "cold.db")
        if cold["unique_component_sets"] != 1:
            failures.append(
                f"cold callers saw {cold['unique_component_sets']} component-id sets"
            )
        if cold["active_components"] != 4:
            failures.append(f"cold bootstrap active components: {cold['active_components']} != 4")
        if cold["active_kinds"] != sorted(BundledHarnessEvolutionOptimizer.KINDS):
            failures.append(f"cold bootstrap active kinds differ: {cold['active_kinds']}")

        harness = TracingHarness(Path(tmp) / "steady.db")
        warm = harness.profile(DOMAIN, session_key="warm")
        if len(warm.get("component_ids") or []) != 4:
            failures.append("warm profile did not initialize all four Harness components")

        warm_keys = [f"warm-{index}" for index in range(WARMUP_CALLS)]
        await _run_arm(harness, "baseline", warm_keys, main_thread_id=main_thread_id)
        await _run_arm(harness, "candidate", warm_keys, main_thread_id=main_thread_id)

        session_keys = [f"profile-{index}" for index in range(CALLS)]
        pairs: list[dict[str, Any]] = []
        max_ratios: list[float] = []
        p99_ratios: list[float] = []
        wall_ratios: list[float] = []
        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            results: dict[str, dict[str, Any]] = {}
            for mode in order:
                results[mode] = await _run_arm(
                    harness, mode, session_keys, main_thread_id=main_thread_id
                )
            baseline = results["baseline"]
            candidate = results["candidate"]
            if baseline["outputs"] != candidate["outputs"]:
                failures.append(f"experiment {experiment}: profile outputs differ")
            if not baseline["ran_on_main_thread"]:
                failures.append(f"experiment {experiment}: baseline did not run on main thread")
            if candidate["ran_on_main_thread"]:
                failures.append(f"experiment {experiment}: candidate ran on main thread")

            base_wall = float(baseline["wall_seconds"])
            cand_wall = float(candidate["wall_seconds"])
            base_max = float(baseline["heartbeat_max_ms"])
            cand_max = float(candidate["heartbeat_max_ms"])
            base_p99 = float(baseline["heartbeat_p99_ms"])
            cand_p99 = float(candidate["heartbeat_p99_ms"])
            wall_ratio = cand_wall / base_wall if base_wall else 0.0
            max_ratio = cand_max / base_max if base_max else 0.0
            p99_ratio = cand_p99 / base_p99 if base_p99 else 0.0
            wall_ratios.append(wall_ratio)
            max_ratios.append(max_ratio)
            p99_ratios.append(p99_ratio)
            pairs.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "baseline_wall_seconds": round(base_wall, 6),
                    "candidate_wall_seconds": round(cand_wall, 6),
                    "wall_ratio": round(wall_ratio, 4),
                    "heartbeat_max_ratio": round(max_ratio, 4),
                    "heartbeat_p99_ratio": round(p99_ratio, 4),
                    "candidate_worker_threads": len(candidate["thread_ids"]),
                }
            )

        wall_median = statistics.median(wall_ratios)
        max_median = statistics.median(max_ratios)
        p99_median = statistics.median(p99_ratios)
        if max_median > MAX_LAG_RATIO_LIMIT:
            failures.append(
                f"median heartbeat max ratio {max_median:.4f} > {MAX_LAG_RATIO_LIMIT:.2f}"
            )
        if wall_median > WALL_RATIO_LIMIT:
            failures.append(
                f"median wall ratio {wall_median:.4f} > {WALL_RATIO_LIMIT:.2f}"
            )

        result = {
            "ok": not failures,
            "cold": {
                "calls": COLD_CALLS,
                "unique_component_sets": cold["unique_component_sets"],
                "active_components": cold["active_components"],
                "active_kinds": cold["active_kinds"],
            },
            "calls_per_arm": CALLS,
            "warmup_calls_per_arm": WARMUP_CALLS,
            "experiments": EXPERIMENTS,
            "semantic_output_equal": not any("profile outputs differ" in x for x in failures),
            "median_heartbeat_max_ratio": round(max_median, 4),
            "median_heartbeat_p99_ratio": round(p99_median, 4),
            "median_wall_ratio": round(wall_median, 4),
            "max_lag_ratio_limit": MAX_LAG_RATIO_LIMIT,
            "wall_ratio_limit": WALL_RATIO_LIMIT,
            "pairs": pairs,
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
