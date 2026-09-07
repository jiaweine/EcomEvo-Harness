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


CALLS = 64
EXPERIMENTS = 5
HEARTBEAT_SECONDS = 0.001
MAX_LAG_RATIO_LIMIT = 0.35
WALL_RATIO_LIMIT = 1.75
DOMAIN = "merchant_review"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def _heartbeat(stop: asyncio.Event, samples_ms: list[float]) -> None:
    loop = asyncio.get_running_loop()
    target = loop.time() + HEARTBEAT_SECONDS
    while not stop.is_set():
        await asyncio.sleep(max(0.0, target - loop.time()))
        now = loop.time()
        samples_ms.append(max(0.0, now - target) * 1000.0)
        target += HEARTBEAT_SECONDS


async def _run_arm(
    harness: BundledHarnessEvolutionOptimizer,
    mode: str,
    session_keys: list[str],
    *,
    main_thread_id: int,
) -> dict[str, Any]:
    thread_ids: set[int] = set()
    heartbeat_samples: list[float] = []
    stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(stop, heartbeat_samples))

    def profile_one(session_key: str) -> dict[str, Any]:
        thread_ids.add(threading.get_ident())
        return harness.profile(DOMAIN, session_key=session_key)

    async def one(session_key: str) -> dict[str, Any]:
        if mode == "baseline":
            return profile_one(session_key)
        return await harness._run_io(profile_one, session_key)

    await asyncio.sleep(0)
    started = time.perf_counter()
    outputs = await asyncio.gather(*(one(session_key) for session_key in session_keys))
    elapsed = time.perf_counter() - started
    stop.set()
    await asyncio.sleep(HEARTBEAT_SECONDS * 2)
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

    return {
        "mode": mode,
        "wall_seconds": elapsed,
        "outputs": outputs,
        "heartbeat_samples_ms": heartbeat_samples,
        "heartbeat_p99_ms": _percentile(heartbeat_samples, 0.99),
        "heartbeat_max_ms": max(heartbeat_samples, default=0.0),
        "thread_ids": sorted(thread_ids),
        "ran_on_main_thread": main_thread_id in thread_ids,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    main_thread_id = threading.get_ident()

    with tempfile.TemporaryDirectory(prefix="ecomevo-harness-profile-offload-") as tmp:
        harness = BundledHarnessEvolutionOptimizer(Path(tmp) / "probe.db")
        # Establish the domain before measurement so both arms exercise the production
        # steady-state read-only profile path rather than UUID-producing bootstrap work.
        warm = harness.profile(DOMAIN, session_key="warm")
        if len(warm.get("component_ids") or []) != 4:
            failures.append("warm profile did not initialize all four harness components")

        session_keys = [f"profile-{index}" for index in range(CALLS)]
        pairs: list[dict[str, Any]] = []
        max_lag_ratios: list[float] = []
        wall_ratios: list[float] = []
        p99_lag_ratios: list[float] = []

        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            results: dict[str, dict[str, Any]] = {}
            for mode in order:
                results[mode] = await _run_arm(
                    harness,
                    mode,
                    session_keys,
                    main_thread_id=main_thread_id,
                )

            baseline = results["baseline"]
            candidate = results["candidate"]
            if baseline["outputs"] != candidate["outputs"]:
                failures.append(f"experiment {experiment}: profile outputs differ")
            if not baseline["ran_on_main_thread"]:
                failures.append(f"experiment {experiment}: sync control did not run on loop thread")
            if candidate["ran_on_main_thread"]:
                failures.append(f"experiment {experiment}: candidate SQLite profile ran on loop thread")

            baseline_max = float(baseline["heartbeat_max_ms"])
            candidate_max = float(candidate["heartbeat_max_ms"])
            baseline_p99 = float(baseline["heartbeat_p99_ms"])
            candidate_p99 = float(candidate["heartbeat_p99_ms"])
            baseline_wall = float(baseline["wall_seconds"])
            candidate_wall = float(candidate["wall_seconds"])
            max_ratio = candidate_max / baseline_max if baseline_max > 0 else 0.0
            p99_ratio = candidate_p99 / baseline_p99 if baseline_p99 > 0 else 0.0
            wall_ratio = candidate_wall / baseline_wall if baseline_wall > 0 else 0.0
            max_lag_ratios.append(max_ratio)
            p99_lag_ratios.append(p99_ratio)
            wall_ratios.append(wall_ratio)
            pairs.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "baseline_wall_seconds": round(baseline_wall, 6),
                    "candidate_wall_seconds": round(candidate_wall, 6),
                    "wall_ratio": round(wall_ratio, 4),
                    "baseline_heartbeat_p99_ms": round(baseline_p99, 4),
                    "candidate_heartbeat_p99_ms": round(candidate_p99, 4),
                    "heartbeat_p99_ratio": round(p99_ratio, 4),
                    "baseline_heartbeat_max_ms": round(baseline_max, 4),
                    "candidate_heartbeat_max_ms": round(candidate_max, 4),
                    "heartbeat_max_ratio": round(max_ratio, 4),
                    "baseline_threads": baseline["thread_ids"],
                    "candidate_threads": candidate["thread_ids"],
                }
            )

        median_max_ratio = statistics.median(max_lag_ratios)
        median_p99_ratio = statistics.median(p99_lag_ratios)
        median_wall_ratio = statistics.median(wall_ratios)
        if median_max_ratio > MAX_LAG_RATIO_LIMIT:
            failures.append(
                f"median heartbeat max ratio {median_max_ratio:.4f} > {MAX_LAG_RATIO_LIMIT:.2f}"
            )
        if median_wall_ratio > WALL_RATIO_LIMIT:
            failures.append(
                f"median wall ratio {median_wall_ratio:.4f} > {WALL_RATIO_LIMIT:.2f}"
            )

        result = {
            "ok": not failures,
            "calls_per_arm": CALLS,
            "experiments": EXPERIMENTS,
            "heartbeat_interval_ms": HEARTBEAT_SECONDS * 1000.0,
            "semantic_output_equal": all(
                pair is not None for pair in pairs
            ) and not any("profile outputs differ" in failure for failure in failures),
            "median_heartbeat_max_ratio": round(median_max_ratio, 4),
            "median_heartbeat_p99_ratio": round(median_p99_ratio, 4),
            "median_wall_ratio": round(median_wall_ratio, 4),
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
