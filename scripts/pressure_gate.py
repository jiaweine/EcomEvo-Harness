from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ecomevo.models import ToolCall
from ecomevo.providers.base import BaseProvider, ProviderInfo
from ecomevo.providers.registry import ProviderRegistry
from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.event_store import EventStore
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer
from ecomevo.runtime.resilient_executor import ResilientPTCExecutor
from ecomevo.runtime.skills import AdaptiveSkillLibrary


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


class _AllowReadSandbox:
    def validate_tool(self, _tool):
        return SimpleNamespace(allowed=True, reason="", requires_confirmation=False)


class _PressureRegistry:
    def __init__(self, tool):
        self.tools = {tool.key: tool}


class _SlowPressureTool:
    key = "pressure.read"
    cost = 0.1

    def __init__(self):
        self.active = 0
        self.peak = 0

    async def execute(self, _ctx, _args):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(1)
            return {"ok": True}
        finally:
            self.active -= 1


async def run_backpressure_probe(tasks: int = 64) -> dict[str, Any]:
    tool = _SlowPressureTool()
    executor = ResilientPTCExecutor(_PressureRegistry(tool), _AllowReadSandbox())
    executor.timeout_s = 0.05
    executor.max_inflight = 2
    executor._slots = asyncio.Semaphore(2)
    calls = [
        ToolCall(
            call_id=f"pressure-{index}",
            tool=tool.key,
            purpose="overload deadline probe",
            parallel_group="overload",
        )
        for index in range(tasks)
    ]
    started = time.perf_counter()
    results = await executor.execute(calls, {})
    wall = time.perf_counter() - started
    failures: list[str] = []
    expected_error = "tool_timeout:0.05s"
    if any(result.error != expected_error for result in results):
        failures.append("overloaded calls did not converge to the bounded timeout result")
    if wall >= 0.75:
        failures.append(f"backpressure queue exceeded total deadline envelope: {wall:.4f}s")
    if tool.peak > 2:
        failures.append(f"tool fan-out exceeded semaphore bound: {tool.peak}")
    if tool.active:
        failures.append(f"cancelled tools remained active: {tool.active}")
    durations = [result.duration_ms for result in results]
    return {
        "name": "ptc_backpressure_deadline",
        "tasks": tasks,
        "max_inflight": 2,
        "deadline_seconds": 0.05,
        "wall_seconds": round(wall, 4),
        "duration_ms_p99": round(percentile(durations, 0.99), 3),
        "peak_active": tool.peak,
        "failures": failures,
    }


class _RoutingProvider(BaseProvider):
    def __init__(
        self,
        key: str,
        *,
        multimodal: bool = False,
        supports_video: bool = False,
        supports_audio: bool = False,
        supports_document: bool = False,
    ):
        self.info = ProviderInfo(
            key=key,
            name=key,
            vendor="pressure",
            model="pressure-model",
            configured=True,
            multimodal=multimodal,
            supports_video=supports_video,
            supports_audio=supports_audio,
            note="pressure fixture",
            supports_document=supports_document,
        )

    async def chat(self, **_kwargs):
        return "ok"


def _routing_pressure_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.providers = {
        "open_model": _RoutingProvider("open_model", multimodal=True),
        "custom": _RoutingProvider("custom", multimodal=True),
        "deepseek": _RoutingProvider("deepseek"),
        "qwen": _RoutingProvider("qwen", multimodal=True, supports_document=True),
        "doubao": _RoutingProvider("doubao", multimodal=True),
        "gemini": _RoutingProvider(
            "gemini",
            multimodal=True,
            supports_video=True,
            supports_audio=True,
            supports_document=True,
        ),
    }
    return registry


async def run_routing_pressure_probe(tasks: int = 6000) -> dict[str, Any]:
    registry = _routing_pressure_registry()
    cases: list[tuple[list[Any], str | None]] = [
        ([], "open_model"),
        ([{"mime": "image/png"}], "open_model"),
        ([{"mime": "video/mp4"}], "gemini"),
        ([{"mime": "Audio/MPEG; codecs=mp3"}], "gemini"),
        ([{"mime": "application/pdf", "meta": {"text": "", "text_density": "not-a-number"}}], "gemini"),
        ([{"mime": "image/webp"}, {"mime": "audio/webm"}], "gemini"),
        ([{"mime": "APPLICATION/PDF; charset=binary", "meta": "corrupt"}], "gemini"),
        (["bad-asset-shape"], "open_model"),
    ]
    latencies_ms: list[float] = []
    failures: list[str] = []

    async def one(index: int) -> None:
        assets, expected = cases[index % len(cases)]
        await asyncio.sleep(0)
        t0 = time.perf_counter()
        chosen = registry.choose("auto", assets)  # type: ignore[arg-type]
        latency = (time.perf_counter() - t0) * 1000
        latencies_ms.append(latency)
        await asyncio.sleep(0)
        current = registry.current_provider()
        key = chosen.info.key if chosen else None
        current_key = current.info.key if current else None
        if key != expected:
            failures.append(f"case={index % len(cases)} expected={expected} got={key}")
        if current_key != key:
            failures.append(f"context leak at task={index}: selected={key} current={current_key}")

    started = time.perf_counter()
    await asyncio.gather(*(one(index) for index in range(tasks)))
    wall = time.perf_counter() - started

    explicit = registry.choose("deepseek", [{"mime": "image/png"}])
    if explicit is not None:
        failures.append("explicit text-only provider accepted a visual asset")
    p99 = percentile(latencies_ms, 0.99)
    if p99 >= 5.0:
        failures.append(f"routing p99 exceeded 5ms envelope: {p99:.3f}ms")
    if wall >= 5.0:
        failures.append(f"routing pressure wall time exceeded 5s envelope: {wall:.3f}s")

    return {
        "name": "multimodal_provider_routing",
        "tasks": tasks,
        "cases": len(cases),
        "wall_seconds": round(wall, 4),
        "throughput_routes_per_second": round(tasks / wall, 1) if wall else 0.0,
        "latency_ms": {
            "p50": round(percentile(latencies_ms, 0.50), 4),
            "p95": round(percentile(latencies_ms, 0.95), 4),
            "p99": round(p99, 4),
            "mean": round(statistics.fmean(latencies_ms), 4) if latencies_ms else 0.0,
        },
        "failures": failures[:20],
        "failure_count": len(failures),
    }


class _CoordinatedReadLibrary(AdaptiveSkillLibrary):
    def __init__(self, db_path: Path, barrier: threading.Barrier):
        self._pressure_barrier = barrier
        super().__init__(db_path)

    def policy(self, domain: str):
        value = super().policy(domain)
        self._pressure_barrier.wait(timeout=5)
        return value


def run_policy_contention_probe(root: Path, workers: int = 8) -> dict[str, Any]:
    db = root / "adaptive-policy-contention.db"
    barrier = threading.Barrier(workers)
    libraries = [_CoordinatedReadLibrary(db, barrier) for _ in range(workers)]
    started = time.perf_counter()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                library.note_run,
                "merchant_review",
                success=False,
                skill_used=False,
            )
            for library in libraries
        ]
        for future in futures:
            try:
                future.result(timeout=10)
            except Exception as exc:
                failures.append(f"policy worker failed: {exc!r}")
    policy = AdaptiveSkillLibrary(db).policy("merchant_review")
    expected_exploration = 0.60 + workers * 0.025
    if policy["updates"] != workers:
        failures.append(f"policy updates lost: {policy['updates']} != {workers}")
    if abs(policy["exploration"] - expected_exploration) > 1e-9:
        failures.append(
            f"policy value lost an update: {policy['exploration']:.6f} != {expected_exploration:.6f}"
        )
    return {
        "name": "adaptive_policy_contention",
        "workers": workers,
        "wall_seconds": round(time.perf_counter() - started, 4),
        "updates": policy["updates"],
        "exploration": round(policy["exploration"], 6),
        "failures": failures,
    }


def run_skill_batch_probe(root: Path, skills: int = 6, rounds: int = 48) -> dict[str, Any]:
    """One multi-skill verifier outcome should remain one policy-learning round."""
    db = root / "skill-batch.db"
    library = AdaptiveSkillLibrary(db)
    rows = [
        library.upsert_candidate(
            domain="merchant_review",
            name=f"pressure-skill-{index}",
            guidance=f"pressure guidance {index}",
            preferred_tools=["merchant.inspect"],
            trigger_terms=[f"pressure-{index}"],
            shadow_score=0.95,
            promote=True,
        )
        for index in range(skills)
    ]
    skill_ids = [row.skill_id for row in rows]
    before = library.policy("merchant_review")
    failures: list[str] = []
    started = time.perf_counter()
    for index in range(rounds):
        updated = library.record_outcome(
            skill_ids,
            success=True,
            score=0.92,
            session_id=f"pressure-skill-round-{index}",
            context={"round": index},
        )
        if len(updated) != skills:
            failures.append(f"round {index}: updated {len(updated)} skills, expected {skills}")
            break
    wall = time.perf_counter() - started
    policy = library.policy("merchant_review")
    if policy["updates"] != before["updates"] + rounds:
        failures.append(
            f"policy write amplification detected: updates={policy['updates']} expected={before['updates'] + rounds}"
        )
    with sqlite3.connect(db) as connection:
        outcome_count = int(connection.execute("SELECT COUNT(*) FROM skill_outcomes").fetchone()[0])
    expected_outcomes = skills * rounds
    if outcome_count != expected_outcomes:
        failures.append(f"skill outcomes lost: {outcome_count} != {expected_outcomes}")
    final_rows = [library.get(skill_id) for skill_id in skill_ids]
    if any(row is None or row.uses != rounds for row in final_rows):
        failures.append("skill posterior uses did not advance exactly once per learning round")
    # This is intentionally generous; it catches accidental return to per-skill writer
    # fan-out without depending on hosted-runner microbenchmark precision.
    if wall >= 2.5:
        failures.append(f"batched skill learning exceeded 2.5s envelope: {wall:.3f}s")
    return {
        "name": "skill_outcome_write_amplification",
        "skills": skills,
        "rounds": rounds,
        "outcomes": outcome_count,
        "policy_updates": policy["updates"] - before["updates"],
        "wall_seconds": round(wall, 4),
        "outcomes_per_policy_update": round(
            outcome_count / max(1, policy["updates"] - before["updates"]), 3
        ),
        "failures": failures,
    }


def run_shared_db_read_isolation_probe(root: Path, reads: int = 256, workers: int = 32) -> dict[str, Any]:
    """Established adaptive reads must not queue behind SQLite's single writer slot."""
    db = root / "shared-read-isolation.db"
    skills = AdaptiveSkillLibrary(db)
    harness = HarnessEvolutionOptimizer(db)
    expected_policy = skills.policy("merchant_review")
    expected_profile = harness.profile("merchant_review", session_key="pressure")
    latencies_ms: list[float] = []
    failures: list[str] = []

    def read(index: int):
        started = time.perf_counter()
        if index % 2:
            value = skills.policy("merchant_review")
            ok = value == expected_policy
        else:
            value = harness.profile("merchant_review", session_key="pressure")
            ok = value == expected_profile
        latencies_ms.append((time.perf_counter() - started) * 1000)
        return ok

    writer = sqlite3.connect(db, timeout=30)
    writer.execute("PRAGMA busy_timeout=30000")
    writer.execute("BEGIN IMMEDIATE")
    started = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(read, index) for index in range(reads)]
    try:
        done, pending = wait(futures, timeout=0.75)
        wall = time.perf_counter() - started
        if pending:
            failures.append(f"{len(pending)} established reads waited behind an unrelated writer")
    finally:
        writer.rollback()
        writer.close()
    for future in futures:
        try:
            if future.result(timeout=2) is not True:
                failures.append("shared-db read returned an inconsistent snapshot")
        except Exception as exc:
            failures.append(f"shared-db read failed: {exc!r}")
    pool.shutdown(wait=True)
    p99 = percentile(latencies_ms, 0.99)
    if p99 >= 250.0:
        failures.append(f"shared-db read p99 exceeded 250ms: {p99:.3f}ms")
    return {
        "name": "shared_db_read_isolation",
        "reads": reads,
        "workers": workers,
        "wall_seconds_before_writer_release": round(wall, 4),
        "latency_ms": {
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "p99": round(p99, 3),
        },
        "failures": failures[:20],
    }


def run_event_store_contention_probe(root: Path, events: int = 256, workers: int = 16) -> dict[str, Any]:
    db = root / "event-store-contention.db"
    owner = EventStore(db)
    owner.create_session("shared-pressure")
    stores = [EventStore(db) for _ in range(8)]
    latencies_ms: list[float] = []
    failures: list[str] = []

    def append(index: int):
        started = time.perf_counter()
        event = stores[index % len(stores)].append("shared-pressure", "pressure.event", {"index": index})
        latencies_ms.append((time.perf_counter() - started) * 1000)
        return event.seq

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            seqs = list(pool.map(append, range(events)))
        except Exception as exc:
            seqs = []
            failures.append(f"event append contention failed: {exc!r}")
    wall = time.perf_counter() - started
    if sorted(seqs) != list(range(1, events + 1)):
        failures.append("concurrent event sequence was not unique and contiguous")
    if not owner.verify_chain("shared-pressure"):
        failures.append("concurrent event hash chain failed verification")
    checkpoint = owner.save_checkpoint("shared-pressure", {"events": events})
    if checkpoint["seq"] != events:
        failures.append(f"checkpoint tail mismatch: {checkpoint['seq']} != {events}")
    sessions = owner.list_sessions(limit=1)
    if not sessions or sessions[0]["event_count"] != events or not sessions[0]["hash_chain_valid"]:
        failures.append("batched session inspection lost event count or chain integrity")
    p99 = percentile(latencies_ms, 0.99)
    throughput = events / wall if wall else 0.0
    # #39's healthy hosted-runner samples were ~760-950 events/s with p99 below
    # 200ms. These envelopes leave >4x headroom while catching the rejected #41
    # long-connection regression (~105 events/s, p99 >1s).
    if wall >= 1.6:
        failures.append(f"event-store contention wall time exceeded 1.6s: {wall:.3f}s")
    if throughput < 160.0:
        failures.append(f"event-store throughput fell below 160 events/s: {throughput:.1f}")
    if p99 >= 800.0:
        failures.append(f"event-store append p99 exceeded 800ms: {p99:.3f}ms")
    return {
        "name": "event_store_contention",
        "events": events,
        "workers": workers,
        "wall_seconds": round(wall, 4),
        "throughput_events_per_second": round(throughput, 1),
        "append_latency_ms": {
            "p50": round(percentile(latencies_ms, 0.50), 3),
            "p95": round(percentile(latencies_ms, 0.95), 3),
            "p99": round(p99, 3),
        },
        "failures": failures,
    }


async def run_level(level: int, root: Path) -> dict[str, Any]:
    db = root / f"runtime-{level}.db"
    engine = EcomEvoEngine(db)
    started = time.perf_counter()

    async def one(index: int):
        t0 = time.perf_counter()
        summary = await engine.run(
            f"审核商家并核对主体、授权和历史风险。压力任务编号 {index}。",
            [],
            domain_hint="merchant_review",
        )
        return summary, time.perf_counter() - t0

    rows = await asyncio.gather(*(one(i) for i in range(level)))
    wall = time.perf_counter() - started
    latencies = [elapsed for _, elapsed in rows]
    summaries = [summary for summary, _ in rows]
    failures: list[str] = []
    session_ids = [summary.session_id for summary in summaries]
    if len(set(session_ids)) != len(session_ids):
        failures.append("duplicate session id")
    for index, summary in enumerate(summaries):
        if not summary.event_chain_valid:
            failures.append(f"{index}: invalid event chain")
        if not summary.stop_reason:
            failures.append(f"{index}: empty stop reason")
        if summary.tool_cost_used > summary.tool_cost_budget + 1e-9:
            failures.append(f"{index}: tool budget exceeded")
        if summary.status not in {"completed", "needs_evidence"}:
            failures.append(f"{index}: invalid status {summary.status}")
        if summary.status != "completed" and summary.proposed_actions:
            failures.append(f"{index}: incomplete evidence produced actions")
        for action in summary.proposed_actions:
            if action.status != "proposed":
                failures.append(f"{index}: autonomous action status {action.status}")
            if action.side_effect and not action.requires_confirmation:
                failures.append(f"{index}: side effect without confirmation")
    return {
        "concurrency": level,
        "tasks": level,
        "wall_seconds": round(wall, 4),
        "throughput_tasks_per_second": round(level / wall, 3) if wall else 0.0,
        "latency_seconds": {
            "p50": round(percentile(latencies, 0.50), 4),
            "p95": round(percentile(latencies, 0.95), 4),
            "p99": round(percentile(latencies, 0.99), 4),
            "mean": round(statistics.fmean(latencies), 4) if latencies else 0.0,
        },
        "failures": failures,
    }


async def main_async(levels: list[int]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ecomevo-pressure-") as tmp:
        root = Path(tmp)
        probes = [
            await run_backpressure_probe(),
            await run_routing_pressure_probe(),
            await asyncio.to_thread(run_policy_contention_probe, root),
            await asyncio.to_thread(run_skill_batch_probe, root),
            await asyncio.to_thread(run_shared_db_read_isolation_probe, root),
            await asyncio.to_thread(run_event_store_contention_probe, root),
        ]
        results = []
        for level in levels:
            results.append(await run_level(level, root))
        failures = [
            f"probe={row['name']}: {failure}"
            for row in probes
            for failure in row["failures"]
        ]
        failures.extend(
            f"c={row['concurrency']}: {failure}"
            for row in results
            for failure in row["failures"]
        )
        return {
            "ok": not failures,
            "levels": levels,
            "probes": probes,
            "results": results,
            "failures": failures,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Current-head EcomEvo runtime pressure/safety gate")
    parser.add_argument("--levels", default="1,8,32,64,120,240")
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",") if value.strip()]
    if not levels or any(level < 1 or level > 512 for level in levels):
        raise SystemExit("levels must be between 1 and 512")
    result = asyncio.run(main_async(levels))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
