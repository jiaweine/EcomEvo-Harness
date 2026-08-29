from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import statistics
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class MethodProfiler:
    """Pressure-only method timer and event-loop synchronous-stall attribution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, list[float]] = defaultdict(list)
        self._sync_intervals: list[tuple[str, float, float]] = []
        self._loop_thread_id = threading.get_ident()

    def record(self, label: str, elapsed_ms: float) -> None:
        with self._lock:
            self._samples[label].append(elapsed_ms)

    def record_sync_interval(self, label: str, started: float, ended: float) -> None:
        # Only work executed on the event-loop thread can directly create a heartbeat stall.
        # Worker-thread intervals (for example grouped EventStore persistence) are excluded.
        if threading.get_ident() != self._loop_thread_id:
            return
        with self._lock:
            self._sync_intervals.append((label, started, ended))

    def wrap(self, obj: Any, method_name: str, label: str) -> None:
        original = getattr(obj, method_name, None)
        if not callable(original):
            return
        if inspect.iscoroutinefunction(original):
            async def async_wrapped(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return await original(*args, **kwargs)
                finally:
                    self.record(label, (time.perf_counter() - started) * 1000.0)

            setattr(obj, method_name, async_wrapped)
            return

        def sync_wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                ended = time.perf_counter()
                self.record(label, (ended - started) * 1000.0)
                self.record_sync_interval(label, started, ended)

        setattr(obj, method_name, sync_wrapped)

    def report(self, *, tasks: int, wall_seconds: float) -> list[dict[str, Any]]:
        with self._lock:
            samples = {label: list(values) for label, values in self._samples.items()}
        rows = []
        for label, values in samples.items():
            total_ms = sum(values)
            rows.append(
                {
                    "method": label,
                    "calls": len(values),
                    "calls_per_task": round(len(values) / max(1, tasks), 3),
                    "total_ms": round(total_ms, 3),
                    "ms_per_task": round(total_ms / max(1, tasks), 3),
                    "latency_ms": {
                        "p50": round(percentile(values, 0.50), 3),
                        "p95": round(percentile(values, 0.95), 3),
                        "p99": round(percentile(values, 0.99), 3),
                        "mean": round(statistics.fmean(values), 3),
                        "max": round(max(values), 3),
                    },
                    "wall_equivalent_pct": round(
                        100.0 * total_ms / max(1.0, wall_seconds * 1000.0), 2
                    ),
                }
            )
        rows.sort(key=lambda row: row["total_ms"], reverse=True)
        return rows

    @staticmethod
    def _covered_ms(segments: list[tuple[float, float]]) -> float:
        if not segments:
            return 0.0
        ordered = sorted(segments)
        start, end = ordered[0]
        covered = 0.0
        for next_start, next_end in ordered[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                covered += end - start
                start, end = next_start, next_end
        covered += end - start
        return covered * 1000.0

    def stall_report(
        self,
        windows: list[tuple[float, float]],
        *,
        minimum_lag_ms: float = 5.0,
    ) -> dict[str, Any]:
        """Attribute heartbeat delay windows to synchronous loop-thread methods.

        A single stall may contain many short methods from different tasks. Per-label overlap
        can therefore expose phase-aligned bursts even when every individual call is fast.
        Union coverage is used for the unattributed total so nested wrappers cannot double
        count the stall window itself.
        """
        with self._lock:
            intervals = list(self._sync_intervals)

        label_overlap_ms: dict[str, float] = defaultdict(float)
        label_hits: dict[str, int] = defaultdict(int)
        large_windows: list[dict[str, Any]] = []
        total_stall_ms = 0.0
        total_covered_ms = 0.0

        for target, woke in windows:
            lag_ms = max(0.0, (woke - target) * 1000.0)
            if lag_ms < minimum_lag_ms:
                continue
            total_stall_ms += lag_ms
            segments: list[tuple[float, float]] = []
            local: dict[str, float] = defaultdict(float)
            for label, started, ended in intervals:
                overlap_start = max(target, started)
                overlap_end = min(woke, ended)
                if overlap_end <= overlap_start:
                    continue
                overlap_ms = (overlap_end - overlap_start) * 1000.0
                local[label] += overlap_ms
                label_overlap_ms[label] += overlap_ms
                label_hits[label] += 1
                segments.append((overlap_start, overlap_end))
            covered_ms = self._covered_ms(segments)
            total_covered_ms += min(lag_ms, covered_ms)
            large_windows.append(
                {
                    "lag_ms": round(lag_ms, 3),
                    "covered_sync_ms": round(min(lag_ms, covered_ms), 3),
                    "unattributed_ms": round(max(0.0, lag_ms - covered_ms), 3),
                    "top_sync_methods": [
                        {"method": label, "overlap_ms": round(value, 3)}
                        for label, value in sorted(local.items(), key=lambda item: item[1], reverse=True)[:6]
                    ],
                }
            )

        large_windows.sort(key=lambda row: row["lag_ms"], reverse=True)
        labels = [
            {
                "method": label,
                "overlap_ms": round(value, 3),
                "stall_window_pct": round(100.0 * value / max(0.001, total_stall_ms), 2),
                "stall_windows": int(label_hits[label]),
            }
            for label, value in label_overlap_ms.items()
        ]
        labels.sort(key=lambda row: row["overlap_ms"], reverse=True)
        return {
            "minimum_lag_ms": minimum_lag_ms,
            "stall_windows": len(large_windows),
            "total_stall_ms": round(total_stall_ms, 3),
            "covered_sync_ms": round(total_covered_ms, 3),
            "unattributed_ms": round(max(0.0, total_stall_ms - total_covered_ms), 3),
            "sync_coverage_pct": round(100.0 * total_covered_ms / max(0.001, total_stall_ms), 2),
            "top_sync_methods": labels[:20],
            "largest_stalls": large_windows[:12],
        }


def attach_profiler(engine: EcomEvoEngine, profiler: MethodProfiler) -> None:
    targets: list[tuple[Any, str, str]] = []

    for name in (
        "create_session_events_checkpoint",
        "create_session_and_append",
        "append",
        "append_grouped",
        "save_checkpoint",
        "save_checkpoint_and_append",
        "restore_checkpoint",
        "save_patch_if_novel",
        "verify_chain",
    ):
        targets.append((engine.events, name, f"events.{name}"))

    for name in ("policy", "relevant", "record_outcome", "note_run"):
        targets.append((engine.skills, name, f"skills.{name}"))

    for name in ("profile", "record_outcome", "propose", "snapshot"):
        targets.append((engine.harness, name, f"harness.{name}"))

    for name in ("relevant", "add"):
        targets.append((engine.memory, name, f"memory.{name}"))

    for name in ("parse_goal", "initial_belief", "plan", "evolution_state"):
        targets.append((engine.planner, name, f"planner.{name}"))

    targets.append((engine.verifier, "verify", "verifier.verify"))
    targets.append((engine.autonomy, "run", "autonomy.run"))
    targets.append((engine.autonomy, "_fingerprint", "autonomy.fingerprint"))
    targets.append((engine.ptc, "execute", "ptc.execute"))
    targets.append((engine.ptc, "_one", "ptc._one"))
    targets.append((engine.autonomy.delegator, "review", "delegator.review"))

    policy = engine.autonomy.policy
    for name in ("observation", "tool_catalog", "sanitize", "fallback_calls", "learn_marginal"):
        targets.append((policy, name, f"policy.{name}"))
    routing = getattr(policy, "routing", None)
    if routing is not None:
        for name in ("prepare_context", "score_prepared", "apply_batch", "snapshot"):
            targets.append((routing, name, f"routing.{name}"))

    registry = engine.tools
    for name in ("planned_calls", "describe"):
        targets.append((registry, name, f"tools.{name}"))

    # Separating actual local-tool execution from ptc._one makes scheduler/semaphore
    # waiting visible. Default pressure tasks use only these in-process read tools.
    for key, tool in engine.tools.tools.items():
        targets.append((tool, "execute", f"tool.{key}.execute"))

    for name in ("evolve", "ingest", "distill_success"):
        targets.append((engine.evolver, name, f"evolver.{name}"))

    for obj, method_name, label in targets:
        profiler.wrap(obj, method_name, label)


async def run_profile(tasks: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ecomevo-runtime-profile-") as tmp:
        root = Path(tmp)
        db = root / "runtime-profile.db"
        engine = EcomEvoEngine(db)
        profiler = MethodProfiler()
        attach_profiler(engine, profiler)

        failures: list[str] = []
        latencies: list[float] = []
        loop_lag_ms: list[float] = []
        loop_lag_windows: list[tuple[float, float]] = []
        stop_heartbeat = asyncio.Event()

        async def heartbeat() -> None:
            interval = 0.01
            target = time.perf_counter() + interval
            while not stop_heartbeat.is_set():
                await asyncio.sleep(max(0.0, target - time.perf_counter()))
                now = time.perf_counter()
                loop_lag_windows.append((target, now))
                loop_lag_ms.append(max(0.0, now - target) * 1000.0)
                target = now + interval

        async def one(index: int) -> None:
            started = time.perf_counter()
            summary = await engine.run(
                f"审核商家并核对主体、授权和历史风险。阶段画像任务 {index}。",
                [],
                domain_hint="merchant_review",
            )
            latencies.append(time.perf_counter() - started)
            if not summary.event_chain_valid:
                failures.append(f"{index}: invalid event chain")
            if not summary.stop_reason:
                failures.append(f"{index}: empty stop reason")

        heartbeat_task = asyncio.create_task(heartbeat())
        started = time.perf_counter()
        try:
            await asyncio.gather(*(one(index) for index in range(tasks)))
        finally:
            stop_heartbeat.set()
            await heartbeat_task
        wall = time.perf_counter() - started
        rows = profiler.report(tasks=tasks, wall_seconds=wall)
        stall_attribution = profiler.stall_report(loop_lag_windows)

        # Timers overlap by design: autonomy contains PTC, and PTC contains _one/tool calls.
        # The actionable signal is leaf execution plus queue/scheduler tails, not percentages summed.
        return {
            "ok": not failures,
            "tasks": tasks,
            "wall_seconds": round(wall, 4),
            "throughput_tasks_per_second": round(tasks / wall, 3) if wall else 0.0,
            "task_latency_seconds": {
                "p50": round(percentile(latencies, 0.50), 4),
                "p95": round(percentile(latencies, 0.95), 4),
                "p99": round(percentile(latencies, 0.99), 4),
                "mean": round(statistics.fmean(latencies), 4) if latencies else 0.0,
            },
            "event_loop_lag_ms": {
                "samples": len(loop_lag_ms),
                "p50": round(percentile(loop_lag_ms, 0.50), 3),
                "p95": round(percentile(loop_lag_ms, 0.95), 3),
                "p99": round(percentile(loop_lag_ms, 0.99), 3),
                "max": round(max(loop_lag_ms), 3) if loop_lag_ms else 0.0,
            },
            "event_loop_stall_attribution": stall_attribution,
            "top_methods": rows,
            "failures": failures[:20],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile runtime method hotspots under concurrent load")
    parser.add_argument("--tasks", type=int, default=120)
    args = parser.parse_args()
    if args.tasks < 1 or args.tasks > 512:
        raise SystemExit("tasks must be between 1 and 512")
    result = asyncio.run(run_profile(args.tasks))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
