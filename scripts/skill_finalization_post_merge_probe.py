"""One fixed diagnostic of #110's first post-merge Skill wall failure.

No production patch, retries, outlier exclusion, or threshold tuning. Six orders
cover every permutation of sync, pre-#110 offload, and current group dispatch.
The original failing 64-call job remains the cold, single-pair observation.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from skill_finalization_gate import DOMAIN, HEARTBEAT_INTERVAL, TracingSkills


ORDERS = (
    ("sync", "legacy_async", "production_async"),
    ("production_async", "legacy_async", "sync"),
    ("legacy_async", "sync", "production_async"),
    ("production_async", "sync", "legacy_async"),
    ("sync", "production_async", "legacy_async"),
    ("legacy_async", "production_async", "sync"),
)
COUNTS = (64, 256)
WALL_LIMIT = 1.75
LAG_LIMIT = 0.35


class TimedSkills(TracingSkills):
    def __init__(self, path):
        self.worker_seconds = 0.0
        self.worker_calls = 0
        super().__init__(path)

    async def _run_io(self, call, /, *args, **kwargs):
        def timed():
            started = time.perf_counter()
            try:
                return call(*args, **kwargs)
            finally:
                self.worker_seconds += time.perf_counter() - started
                self.worker_calls += 1

        return await super()._run_io(timed)


class LegacySkills(TimedSkills):
    async def note_run_async(self, domain, *, success, skill_used=False):
        # Exact pre-#110 dispatch, retained only as an attribution control.
        self._invalidate_decision_policy_snapshot()
        await self._run_io(self.note_run, domain, success=success, skill_used=skill_used)


async def measure(mode: str, root: Path, count: int) -> dict:
    cls = LegacySkills if mode == "legacy_async" else TimedSkills
    skills = cls(root / f"{mode}.db")
    before = skills.policy(DOMAIN)
    skills.immediate_begins = 0
    lags: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        target = time.perf_counter() + HEARTBEAT_INTERVAL
        while not stop.is_set():
            await asyncio.sleep(max(0.0, target - time.perf_counter()))
            now = time.perf_counter()
            lags.append(max(0.0, now - target) * 1000.0)
            target = now + HEARTBEAT_INTERVAL

    async def one():
        if mode == "sync":
            started = time.perf_counter()
            try:
                skills.note_run(DOMAIN, success=False, skill_used=False)
            finally:
                skills.worker_seconds += time.perf_counter() - started
                skills.worker_calls += 1
        else:
            await skills.note_run_async(DOMAIN, success=False, skill_used=False)

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(HEARTBEAT_INTERVAL * 2)
    started = time.perf_counter()
    try:
        await asyncio.gather(*(one() for _ in range(count)))
    finally:
        wall = time.perf_counter() - started
        stop.set()
        await task
    after = skills.policy(DOMAIN)
    assert skills.immediate_begins == count, (mode, skills.immediate_begins, count)
    assert after["updates"] == before["updates"] + count
    assert skills.worker_calls == count
    return {
        "mode": mode,
        "tasks": count,
        "writer_transactions": skills.immediate_begins,
        "policy": {k: v for k, v in after.items() if k != "updated_at"},
        "wall_seconds": wall,
        "heartbeat_max_ms": max(lags, default=0.0),
        "worker_seconds": skills.worker_seconds,
        "outside_worker_seconds": wall - skills.worker_seconds,
        "worker_calls": skills.worker_calls,
    }


async def main_async():
    # Explicit steady-state attribution; executor startup is outside measurement
    # for every arm. Each measurement still gets a fresh identically seeded DB.
    await asyncio.to_thread(lambda: None)
    results = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-post-merge-") as tmp:
        root = Path(tmp)
        for count in COUNTS:
            samples = []
            for index, order in enumerate(ORDERS):
                sample_root = root / f"{count}-{index}"
                sample_root.mkdir()
                arms = {}
                for mode in order:
                    arms[mode] = await measure(mode, sample_root, count)
                assert arms["sync"]["policy"] == arms["legacy_async"]["policy"]
                assert arms["sync"]["policy"] == arms["production_async"]["policy"]
                comparisons = {}
                for mode in ("legacy_async", "production_async"):
                    comparisons[mode] = {
                        "wall_ratio": arms[mode]["wall_seconds"] / arms["sync"]["wall_seconds"],
                        "lag_ratio": arms[mode]["heartbeat_max_ms"] / max(0.001, arms["sync"]["heartbeat_max_ms"]),
                    }
                sample = {"round": index + 1, "order": order, "arms": arms, "comparison": comparisons}
                samples.append(sample)
                print(json.dumps({"sample": sample}, sort_keys=True), flush=True)
            summary = {}
            for mode in ("legacy_async", "production_async"):
                wall = statistics.median(s["comparison"][mode]["wall_ratio"] for s in samples)
                lag = statistics.median(s["comparison"][mode]["lag_ratio"] for s in samples)
                summary[mode] = {"wall_ratio_median": wall, "lag_ratio_median": lag,
                                 "within_original_limits": wall <= WALL_LIMIT and lag <= LAG_LIMIT}
            results.append({"tasks_per_arm": count, "rounds": len(samples), "summary": summary, "samples": samples})
    print(json.dumps({"diagnostic_only": True, "wall_limit": WALL_LIMIT, "lag_limit": LAG_LIMIT,
                      "original_failure": {"run_id": 34251367383, "wall_ratio": 2.8032, "preserved": True},
                      "results": results}, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main_async())
