from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ecomevo.runtime.engine import EcomEvoEngine


TASKS = 32
DOMAIN = "merchant_review"


async def _run_batch(engine: EcomEvoEngine, tasks: int) -> list[Any]:
    async def one(index: int):
        return await engine.run(
            f"审核商家并核对主体、授权和历史风险。empty rank probe 任务 {index}。",
            [],
            domain_hint=DOMAIN,
        )

    return await asyncio.gather(*(one(index) for index in range(tasks)))


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-empty-rank-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "runtime.db")
        policy = engine.autonomy.policy
        policy_class = type(policy).__name__
        if policy_class != "CounterfactualAdaptiveDecisionPolicy":
            failures.append(f"unexpected production policy class: {policy_class}")

        warm = await _run_batch(engine, 1)
        if not warm[0].event_chain_valid:
            failures.append("warm-up run produced invalid event chain")

        rank_rows: list[dict[str, Any]] = []
        sanitize_rows: list[dict[str, Any]] = []
        fallback_rows: list[dict[str, Any]] = []
        prepare_calls = 0
        score_calls = 0

        original_prepare = policy.routing.prepare_context
        original_score = policy.routing.score_prepared
        original_rank = policy._rank_candidates
        original_sanitize = policy.sanitize
        original_fallback = policy.fallback_calls

        def prepare_context(*args, **kwargs):
            nonlocal prepare_calls
            prepare_calls += 1
            return original_prepare(*args, **kwargs)

        def score_prepared(*args, **kwargs):
            nonlocal score_calls
            score_calls += 1
            return original_score(*args, **kwargs)

        def rank_candidates(candidates, *args, **kwargs):
            before_prepare = prepare_calls
            before_score = score_calls
            started = time.perf_counter()
            selected, trace = original_rank(candidates, *args, **kwargs)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            rank_rows.append(
                {
                    "candidate_count": len(candidates),
                    "selected_count": len(selected),
                    "trace_count": len(trace),
                    "prepare_calls": prepare_calls - before_prepare,
                    "score_calls": score_calls - before_score,
                    "wall_ms": elapsed_ms,
                }
            )
            return selected, trace

        def sanitize(raw, *args, **kwargs):
            started = time.perf_counter()
            result = original_sanitize(raw, *args, **kwargs)
            sanitize_rows.append(
                {
                    "phase": str(kwargs.get("phase") or ""),
                    "raw_has_tool_calls": bool(
                        isinstance(raw, dict) and (raw.get("tool_calls") or [])
                    ),
                    "selected_calls": len(result.calls),
                    "wall_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
            return result

        def fallback_calls(*args, **kwargs):
            started = time.perf_counter()
            result = original_fallback(*args, **kwargs)
            fallback_rows.append(
                {
                    "selected_calls": len(result),
                    "wall_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
            return result

        policy.routing.prepare_context = prepare_context
        policy.routing.score_prepared = score_prepared
        policy._rank_candidates = rank_candidates
        policy.sanitize = sanitize
        policy.fallback_calls = fallback_calls

        started = time.perf_counter()
        summaries = await _run_batch(engine, TASKS)
        runtime_wall = time.perf_counter() - started
        if any(not summary.event_chain_valid for summary in summaries):
            failures.append("measured run produced invalid event chain")

    size_counts = Counter(row["candidate_count"] for row in rank_rows)
    empty_rows = [row for row in rank_rows if row["candidate_count"] == 0]
    nonempty_rows = [row for row in rank_rows if row["candidate_count"] > 0]

    def total_ms(rows: list[dict[str, Any]]) -> float:
        return sum(float(row["wall_ms"]) for row in rows)

    empty_prepare = sum(int(row["prepare_calls"]) for row in empty_rows)
    empty_score = sum(int(row["score_calls"]) for row in empty_rows)
    empty_share = len(empty_rows) / max(1, len(rank_rows))
    empty_wall_share = total_ms(empty_rows) / max(1e-9, total_ms(rank_rows))

    return {
        "ok": not failures,
        "policy_class": policy_class,
        "tasks": TASKS,
        "runtime_wall_seconds": round(runtime_wall, 4),
        "rank_calls": len(rank_rows),
        "rank_calls_per_task": round(len(rank_rows) / TASKS, 3),
        "candidate_count_distribution": {
            str(key): value for key, value in sorted(size_counts.items())
        },
        "empty_rank": {
            "calls": len(empty_rows),
            "calls_per_task": round(len(empty_rows) / TASKS, 3),
            "share": round(empty_share, 4),
            "wall_ms_total": round(total_ms(empty_rows), 3),
            "wall_share": round(empty_wall_share, 4),
            "prepare_calls": empty_prepare,
            "score_calls": empty_score,
            "prepare_calls_per_empty_rank": round(
                empty_prepare / max(1, len(empty_rows)), 3
            ),
            "score_calls_per_empty_rank": round(
                empty_score / max(1, len(empty_rows)), 3
            ),
        },
        "nonempty_rank": {
            "calls": len(nonempty_rows),
            "calls_per_task": round(len(nonempty_rows) / TASKS, 3),
            "wall_ms_total": round(total_ms(nonempty_rows), 3),
            "prepare_calls": sum(int(row["prepare_calls"]) for row in nonempty_rows),
            "score_calls": sum(int(row["score_calls"]) for row in nonempty_rows),
        },
        "routing_totals": {
            "prepare_calls": prepare_calls,
            "score_calls": score_calls,
        },
        "sanitize": {
            "calls": len(sanitize_rows),
            "calls_per_task": round(len(sanitize_rows) / TASKS, 3),
            "wall_ms_total": round(total_ms(sanitize_rows), 3),
            "phases": dict(Counter(row["phase"] for row in sanitize_rows)),
            "zero_selected": sum(1 for row in sanitize_rows if row["selected_calls"] == 0),
        },
        "fallback": {
            "calls": len(fallback_rows),
            "calls_per_task": round(len(fallback_rows) / TASKS, 3),
            "wall_ms_total": round(total_ms(fallback_rows), 3),
        },
        "worth_followup": bool(
            empty_share >= 0.25
            and empty_prepare == len(empty_rows)
            and empty_score == len(empty_rows)
        ),
        "failures": failures,
    }


def main() -> int:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
