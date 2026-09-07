from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ecomevo.runtime.engine import EcomEvoEngine


TASKS = 32
EXPERIMENTS = 5
DOMAIN = "merchant_review"
RANK_WALL_RATIO_LIMIT = 0.92


async def _run_batch(engine: EcomEvoEngine, tasks: int) -> list[Any]:
    async def one(index: int):
        return await engine.run(
            f"审核商家并核对主体、授权和历史风险。empty short circuit 任务 {index}。",
            [],
            domain_hint=DOMAIN,
        )

    return await asyncio.gather(*(one(index) for index in range(tasks)))


def _install_probe(policy: Any, *, short_circuit_empty: bool) -> dict[str, Any]:
    state: dict[str, Any] = {
        "prepare_calls": 0,
        "score_calls": 0,
        "rank_rows": [],
    }
    original_prepare = policy.routing.prepare_context
    original_score = policy.routing.score_prepared
    original_rank = policy._rank_candidates

    def prepare_context(*args, **kwargs):
        state["prepare_calls"] += 1
        return original_prepare(*args, **kwargs)

    def score_prepared(*args, **kwargs):
        state["score_calls"] += 1
        return original_score(*args, **kwargs)

    def rank_candidates(candidates, *args, **kwargs):
        before_prepare = int(state["prepare_calls"])
        before_score = int(state["score_calls"])
        started = time.perf_counter()
        if short_circuit_empty and not candidates:
            selected, trace = [], []
        else:
            selected, trace = original_rank(candidates, *args, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        state["rank_rows"].append(
            {
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "trace_count": len(trace),
                "prepare_calls": int(state["prepare_calls"]) - before_prepare,
                "score_calls": int(state["score_calls"]) - before_score,
                "wall_ms": elapsed_ms,
            }
        )
        return selected, trace

    policy.routing.prepare_context = prepare_context
    policy.routing.score_prepared = score_prepared
    policy._rank_candidates = rank_candidates
    return state


def _summarize_state(state: dict[str, Any], runtime_wall: float) -> dict[str, Any]:
    rows = list(state["rank_rows"])
    empty = [row for row in rows if row["candidate_count"] == 0]
    nonempty = [row for row in rows if row["candidate_count"] > 0]

    def wall(items: list[dict[str, Any]]) -> float:
        return sum(float(row["wall_ms"]) for row in items)

    return {
        "runtime_wall_seconds": runtime_wall,
        "rank_calls": len(rows),
        "candidate_count_distribution": dict(
            Counter(int(row["candidate_count"]) for row in rows)
        ),
        "rank_wall_ms_total": wall(rows),
        "empty": {
            "calls": len(empty),
            "wall_ms_total": wall(empty),
            "prepare_calls": sum(int(row["prepare_calls"]) for row in empty),
            "score_calls": sum(int(row["score_calls"]) for row in empty),
        },
        "nonempty": {
            "calls": len(nonempty),
            "wall_ms_total": wall(nonempty),
            "prepare_calls": sum(int(row["prepare_calls"]) for row in nonempty),
            "score_calls": sum(int(row["score_calls"]) for row in nonempty),
        },
        "prepare_calls": int(state["prepare_calls"]),
        "score_calls": int(state["score_calls"]),
    }


async def _arm(root: Path, mode: str, experiment: int) -> dict[str, Any]:
    engine = EcomEvoEngine(root / f"{mode}-{experiment}.db")
    policy = engine.autonomy.policy
    if type(policy).__name__ != "CounterfactualAdaptiveDecisionPolicy":
        raise AssertionError(f"unexpected production policy class: {type(policy).__name__}")

    warm = await _run_batch(engine, 1)
    if not warm[0].event_chain_valid:
        raise AssertionError(f"{mode} warm-up event chain invalid")

    state = _install_probe(policy, short_circuit_empty=(mode == "candidate"))
    started = time.perf_counter()
    summaries = await _run_batch(engine, TASKS)
    runtime_wall = time.perf_counter() - started
    if any(not summary.event_chain_valid for summary in summaries):
        raise AssertionError(f"{mode} measured event chain invalid")

    return _summarize_state(state, runtime_wall)


def _direct_semantics(root: Path) -> dict[str, Any]:
    engine = EcomEvoEngine(root / "direct.db")
    policy = engine.autonomy.policy
    goal = engine.planner.parse_goal(
        "审核商家并核对主体、授权和历史风险。",
        [],
        domain_hint=DOMAIN,
    )
    before = policy._routing_source.snapshot(DOMAIN)
    selected, trace = policy._rank_candidates(
        [],
        goal=goal,
        missing=list(goal.required_evidence),
        previous=[],
        skills=[],
        budget=float(goal.max_tool_cost),
        limit=4,
    )
    after = policy._routing_source.snapshot(DOMAIN)
    return {
        "legacy_selected": selected,
        "legacy_trace": trace,
        "short_circuit_selected": [],
        "short_circuit_trace": [],
        "routing_snapshot_unchanged": before == after,
    }


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    paired: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-empty-short-circuit-") as tmp:
        root = Path(tmp)
        semantics = _direct_semantics(root)
        if semantics["legacy_selected"] != [] or semantics["legacy_trace"] != []:
            failures.append("legacy empty rank did not return exactly ([], [])")
        if not semantics["routing_snapshot_unchanged"]:
            failures.append("legacy empty rank changed persistent routing state")

        for experiment in range(EXPERIMENTS):
            order = (
                ("baseline", "candidate")
                if experiment % 2 == 0
                else ("candidate", "baseline")
            )
            rows: dict[str, dict[str, Any]] = {}
            for mode in order:
                rows[mode] = await _arm(root, mode, experiment)

            baseline = rows["baseline"]
            candidate = rows["candidate"]
            rank_ratio = candidate["rank_wall_ms_total"] / max(
                1e-9, baseline["rank_wall_ms_total"]
            )
            runtime_ratio = candidate["runtime_wall_seconds"] / max(
                1e-9, baseline["runtime_wall_seconds"]
            )
            paired.append(
                {
                    "experiment": experiment,
                    "order": list(order),
                    "rank_wall_ratio": rank_ratio,
                    "runtime_wall_ratio": runtime_ratio,
                    "baseline": baseline,
                    "candidate": candidate,
                }
            )

            baseline_empty = baseline["empty"]
            candidate_empty = candidate["empty"]
            if baseline_empty["calls"] != candidate_empty["calls"]:
                failures.append(
                    f"experiment {experiment}: empty rank count changed "
                    f"{baseline_empty['calls']} -> {candidate_empty['calls']}"
                )
            if baseline_empty["prepare_calls"] != baseline_empty["calls"]:
                failures.append(
                    f"experiment {experiment}: baseline empty prepares "
                    f"{baseline_empty['prepare_calls']} != {baseline_empty['calls']}"
                )
            if baseline_empty["score_calls"] != baseline_empty["calls"]:
                failures.append(
                    f"experiment {experiment}: baseline empty scores "
                    f"{baseline_empty['score_calls']} != {baseline_empty['calls']}"
                )
            if candidate_empty["prepare_calls"] != 0:
                failures.append(
                    f"experiment {experiment}: candidate empty prepares "
                    f"{candidate_empty['prepare_calls']} != 0"
                )
            if candidate_empty["score_calls"] != 0:
                failures.append(
                    f"experiment {experiment}: candidate empty scores "
                    f"{candidate_empty['score_calls']} != 0"
                )

            # The short-circuit is empty-only. The live non-empty operation count shape
            # must remain identical on this deterministic workload.
            if baseline["nonempty"]["calls"] != candidate["nonempty"]["calls"]:
                failures.append(
                    f"experiment {experiment}: nonempty rank count changed "
                    f"{baseline['nonempty']['calls']} -> {candidate['nonempty']['calls']}"
                )
            if baseline["nonempty"]["prepare_calls"] != candidate["nonempty"]["prepare_calls"]:
                failures.append(
                    f"experiment {experiment}: nonempty prepare count changed "
                    f"{baseline['nonempty']['prepare_calls']} -> "
                    f"{candidate['nonempty']['prepare_calls']}"
                )
            if baseline["nonempty"]["score_calls"] != candidate["nonempty"]["score_calls"]:
                failures.append(
                    f"experiment {experiment}: nonempty score count changed "
                    f"{baseline['nonempty']['score_calls']} -> "
                    f"{candidate['nonempty']['score_calls']}"
                )

    rank_ratios = [float(row["rank_wall_ratio"]) for row in paired]
    runtime_ratios = [float(row["runtime_wall_ratio"]) for row in paired]
    median_rank_ratio = float(statistics.median(rank_ratios))
    median_runtime_ratio = float(statistics.median(runtime_ratios))
    if median_rank_ratio > RANK_WALL_RATIO_LIMIT:
        failures.append(
            f"median total-rank wall ratio {median_rank_ratio:.4f} > "
            f"{RANK_WALL_RATIO_LIMIT:.2f}"
        )

    return {
        "ok": not failures,
        "tasks_per_arm": TASKS,
        "experiments": EXPERIMENTS,
        "rank_wall_ratio_limit": RANK_WALL_RATIO_LIMIT,
        "semantics": semantics,
        "rank_wall_ratios": [round(value, 4) for value in rank_ratios],
        "median_rank_wall_ratio": round(median_rank_ratio, 4),
        "runtime_wall_ratios": [round(value, 4) for value in runtime_ratios],
        "median_runtime_wall_ratio": round(median_runtime_ratio, 4),
        "pairs": paired,
        "failures": failures,
    }


def main() -> int:
    report = asyncio.run(main_async())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
