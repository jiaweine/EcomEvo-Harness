from __future__ import annotations

import asyncio
import copy
import json
import statistics
import tempfile
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import ecomevo.runtime.control_policy as control_policy
from ecomevo.runtime import EcomEvoEngine


EXPERIMENTS = 5
WARMUP_TASKS = 20
MEASURED_TASKS = 240
FALLBACKS_PER_TASK = 2
RATIO_LIMIT = 0.95


def _normalize_calls(calls: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in calls:
        data = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        rows.append(
            {
                "tool": data.get("tool"),
                "purpose": data.get("purpose"),
                "args": data.get("args") or {},
                "estimated_cost": data.get("estimated_cost", data.get("cost")),
                "parallel_group": data.get("parallel_group"),
            }
        )
    return rows


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


async def _capture_fallback(engine: EcomEvoEngine) -> tuple[tuple[Any, ...], dict[str, Any]]:
    policy = engine.autonomy.policy
    original = policy.fallback_calls
    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def capture(*args, **kwargs):
        if not captured:
            captured.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
        return original(*args, **kwargs)

    policy.fallback_calls = capture
    try:
        summary = await engine.run(
            "审核商家并核对主体、授权和历史风险。task-local target-term gate。",
            [],
            domain_hint="merchant_review",
        )
    finally:
        policy.fallback_calls = original
    if not summary.event_chain_valid:
        raise AssertionError("capture run produced invalid event chain")
    if not captured:
        raise AssertionError("capture run did not enter fallback_calls")
    return captured[0]


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-task-target-gate-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "gate.db")
        policy = engine.autonomy.policy
        captured_args, captured_kwargs = await _capture_fallback(engine)

        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        mode_var: ContextVar[str] = ContextVar("task-target-gate-mode", default="baseline")
        count_var: ContextVar[bool] = ContextVar("task-target-gate-count", default=False)
        counters: dict[str, Counter[str]] = {
            "baseline": Counter(),
            "candidate": Counter(),
        }
        original_query_terms = control_policy._query_terms

        def counted_query_terms(text: str, limit: int = 24):
            if count_var.get() and int(limit) == 64:
                counters[mode_var.get()]["underlying_limit64"] += 1
            return original_query_terms(text, limit=limit)

        control_policy._query_terms = counted_query_terms
        try:
            # Direct production-boundary checks. Outside a bound task, strings remain
            # uncached. Inside one task, exact values reuse immutable terms and reset cleanly.
            if policy._task_target_terms.get() is not None:
                failures.append("target-term cache unexpectedly active before bind")
            outside = policy._terms("synthetic-target-alpha")
            if policy._task_target_terms.get() is not None:
                failures.append("unbound string term call created task cache")

            token = policy.bind_task_target_terms()
            try:
                first = policy._terms("synthetic-target-alpha")
                if first != outside:
                    failures.append("bound exact-string terms changed output")
                first.add("__mutation_probe__")
                second = policy._terms("synthetic-target-alpha")
                if "__mutation_probe__" in second or second != outside:
                    failures.append("cached terms leaked caller mutation")
                cache = policy._task_target_terms.get()
                if cache is None or set(cache) != {"synthetic-target-alpha"}:
                    failures.append("unexpected exact-string cache contents after first target")
                policy._terms("synthetic-target-beta")
                cache = policy._task_target_terms.get()
                if cache is None or set(cache) != {"synthetic-target-alpha", "synthetic-target-beta"}:
                    failures.append("different target string did not miss cleanly")
            finally:
                policy.reset_task_target_terms(token)
            if policy._task_target_terms.get() is not None:
                failures.append("target-term cache survived explicit task reset")

            def prepare_recovery_round() -> None:
                policy._decision_round.set(None)
                policy.sanitize(
                    None,
                    goal=goal,
                    remaining_budget=remaining_budget,
                    previous=previous,
                    skills=skills,
                    phase="recovery",
                    missing_evidence=list(belief.missing_evidence),
                )

            expected_output: list[dict[str, Any]] | None = None
            per_fallback_ms: dict[str, list[float]] = {"baseline": [], "candidate": []}
            paired_rows: list[dict[str, Any]] = []

            def run_arm(mode: str, *, measured: bool) -> tuple[float, int]:
                nonlocal expected_output
                mode_token = mode_var.set(mode)
                count_token = count_var.set(measured)
                task_count = MEASURED_TASKS if measured else WARMUP_TASKS
                elapsed_total = 0.0
                mismatches = 0
                try:
                    for _task in range(task_count):
                        task_token = policy.bind_task_target_terms() if mode == "candidate" else None
                        try:
                            for _round in range(FALLBACKS_PER_TASK):
                                prepare_recovery_round()
                                started = time.perf_counter()
                                output = policy.fallback_calls(
                                    goal,
                                    belief,
                                    assets,
                                    remaining_budget=remaining_budget,
                                    previous=previous,
                                    skills=skills,
                                )
                                elapsed_ms = (time.perf_counter() - started) * 1000.0
                                normalized = _normalize_calls(output)
                                if expected_output is None:
                                    expected_output = normalized
                                elif normalized != expected_output:
                                    mismatches += 1
                                if measured:
                                    elapsed_total += elapsed_ms
                                    per_fallback_ms[mode].append(elapsed_ms)
                        finally:
                            if task_token is not None:
                                policy.reset_task_target_terms(task_token)
                        if policy._task_target_terms.get() is not None:
                            mismatches += 1
                finally:
                    count_var.reset(count_token)
                    mode_var.reset(mode_token)
                return elapsed_total, mismatches

            # Capture already warmed the existing set-only static metadata cache. Warm both
            # benchmark arms for code/data locality without counting those calls.
            run_arm("baseline", measured=False)
            run_arm("candidate", measured=False)

            for experiment in range(EXPERIMENTS):
                order = (
                    ("baseline", "candidate")
                    if experiment % 2 == 0
                    else ("candidate", "baseline")
                )
                results: dict[str, tuple[float, int]] = {}
                for arm in order:
                    results[arm] = run_arm(arm, measured=True)
                baseline_ms, baseline_mismatches = results["baseline"]
                candidate_ms, candidate_mismatches = results["candidate"]
                if baseline_mismatches or candidate_mismatches:
                    failures.append(
                        f"experiment {experiment} changed normalized output/cache lifetime: "
                        f"baseline={baseline_mismatches}, candidate={candidate_mismatches}"
                    )
                paired_rows.append(
                    {
                        "experiment": experiment,
                        "order": list(order),
                        "baseline_ms": baseline_ms,
                        "candidate_ms": candidate_ms,
                        "ratio": candidate_ms / baseline_ms if baseline_ms else 0.0,
                    }
                )

            baseline_underlying = counters["baseline"]["underlying_limit64"]
            candidate_underlying = counters["candidate"]["underlying_limit64"]
            if baseline_underlying <= 0:
                failures.append("baseline produced no measured limit=64 term work")
            if baseline_underlying % FALLBACKS_PER_TASK:
                failures.append(
                    f"baseline term count {baseline_underlying} is not divisible by task round count"
                )
            expected_candidate = baseline_underlying // FALLBACKS_PER_TASK
            if candidate_underlying != expected_candidate:
                failures.append(
                    "production task cache did not halve repeated target computations: "
                    f"{candidate_underlying} != {expected_candidate}"
                )
            if not expected_output:
                failures.append("fixed fallback input produced no ToolCall output")

            ratios = [float(row["ratio"]) for row in paired_rows]
            median_ratio = statistics.median(ratios)
            if median_ratio > RATIO_LIMIT:
                failures.append(
                    "production task-local target cache missed speed threshold: "
                    f"{median_ratio:.4f}x > {RATIO_LIMIT:.2f}x"
                )

            measured_fallbacks = EXPERIMENTS * MEASURED_TASKS * FALLBACKS_PER_TASK
            result = {
                "ok": not failures,
                "experiments": EXPERIMENTS,
                "warmup_tasks_per_mode": WARMUP_TASKS,
                "measured_tasks_per_mode_per_experiment": MEASURED_TASKS,
                "fallbacks_per_task": FALLBACKS_PER_TASK,
                "measured_fallbacks_per_mode": measured_fallbacks,
                "underlying_limit64_calls": {
                    "baseline": int(baseline_underlying),
                    "candidate": int(candidate_underlying),
                    "ratio": round(candidate_underlying / baseline_underlying, 4)
                    if baseline_underlying else 0.0,
                },
                "paired_operation_ratios": [round(value, 4) for value in ratios],
                "paired_operation_ratio_median": round(median_ratio, 4),
                "ratio_limit": RATIO_LIMIT,
                "per_fallback_latency_ms": {
                    mode: {
                        "p50": round(_percentile(values, 0.50), 4),
                        "p95": round(_percentile(values, 0.95), 4),
                        "mean": round(sum(values) / max(1, len(values)), 4),
                    }
                    for mode, values in per_fallback_ms.items()
                },
                "pairs": [
                    {
                        "experiment": int(row["experiment"]),
                        "order": row["order"],
                        "baseline_ms": round(float(row["baseline_ms"]), 3),
                        "candidate_ms": round(float(row["candidate_ms"]), 3),
                        "ratio": round(float(row["ratio"]), 4),
                    }
                    for row in paired_rows
                ],
                "privacy": {
                    "raw_request_text_emitted": False,
                    "cache_scope": "production ContextVar bound/reset per autonomy task",
                    "cache_key_output": "never emitted",
                    "cross_task_retention": False,
                },
                "failures": failures,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result
        finally:
            control_policy._query_terms = original_query_terms


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
