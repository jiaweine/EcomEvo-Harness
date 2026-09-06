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

from ecomevo.runtime import EcomEvoEngine


EXPERIMENTS = 5
WARMUP_TASKS = 20
MEASURED_TASKS = 240
FALLBACKS_PER_TASK = 2
POSITIVE_RATIO = 0.95


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
            "审核商家并核对主体、授权和历史风险。task-local target-term probe。",
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
    with tempfile.TemporaryDirectory(prefix="ecomevo-task-target-terms-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "probe.db")
        policy = engine.autonomy.policy
        captured_args, captured_kwargs = await _capture_fallback(engine)

        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        mode_var: ContextVar[str] = ContextVar("task-target-mode", default="baseline")
        count_var: ContextVar[bool] = ContextVar("task-target-count", default=False)
        task_cache_var: ContextVar[dict[str, frozenset[str]] | None] = ContextVar(
            "task-target-cache", default=None
        )
        original_terms = policy._terms
        counters: dict[str, Counter[str]] = {
            "baseline": Counter(),
            "candidate": Counter(),
        }

        def wrapped_terms(value: Any):
            mode = mode_var.get()
            if not isinstance(value, str):
                return original_terms(value)

            if count_var.get():
                counters[mode]["string_lookups"] += 1

            cache = task_cache_var.get() if mode == "candidate" else None
            if cache is not None:
                cached = cache.get(value)
                if cached is not None:
                    if count_var.get():
                        counters[mode]["string_hits"] += 1
                    return set(cached)

            if count_var.get():
                counters[mode]["underlying_string_terms"] += 1
            result = original_terms(value)
            if cache is not None:
                cache[value] = frozenset(result)
                if count_var.get():
                    counters[mode]["string_misses"] += 1
            return result

        policy._terms = wrapped_terms

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

        # Boundary checks for the proposed task-local semantics. No raw request text is
        # emitted; these synthetic strings exist only inside the process.
        mode_token = mode_var.set("candidate")
        cache_token = task_cache_var.set({})
        try:
            first = policy._terms("synthetic-target-alpha")
            second = policy._terms("synthetic-target-alpha")
            if first != second:
                failures.append("same-string task-local cache changed term output")
            first.add("__mutation_probe__")
            third = policy._terms("synthetic-target-alpha")
            if "__mutation_probe__" in third:
                failures.append("cached target terms leaked caller mutation")
            before = len(task_cache_var.get() or {})
            policy._terms("synthetic-target-beta")
            after = len(task_cache_var.get() or {})
            if after != before + 1:
                failures.append("different target string did not create a clean cache miss")
        finally:
            task_cache_var.reset(cache_token)
            mode_var.reset(mode_token)
        if task_cache_var.get() is not None:
            failures.append("task-local cache remained visible after reset")

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
                    cache_token = task_cache_var.set({} if mode == "candidate" else None)
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
                        task_cache_var.reset(cache_token)
                    if task_cache_var.get() is not None:
                        mismatches += 1
            finally:
                count_var.reset(count_token)
                mode_var.reset(mode_token)
            return elapsed_total, mismatches

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

        measured_tasks_per_mode = EXPERIMENTS * MEASURED_TASKS
        measured_fallbacks_per_mode = measured_tasks_per_mode * FALLBACKS_PER_TASK
        baseline = counters["baseline"]
        candidate = counters["candidate"]

        if baseline["string_lookups"] != candidate["string_lookups"]:
            failures.append(
                "candidate changed string lookup shape: "
                f"{candidate['string_lookups']} != {baseline['string_lookups']}"
            )
        if baseline["underlying_string_terms"] != baseline["string_lookups"]:
            failures.append(
                "baseline unexpectedly reused string terms: "
                f"{baseline['underlying_string_terms']} != {baseline['string_lookups']}"
            )
        expected_candidate_underlying = baseline["underlying_string_terms"] // FALLBACKS_PER_TASK
        if candidate["underlying_string_terms"] != expected_candidate_underlying:
            failures.append(
                "candidate did not reduce repeated same-task string work by two rounds: "
                f"{candidate['underlying_string_terms']} != {expected_candidate_underlying}"
            )
        expected_hits = candidate["string_lookups"] - candidate["underlying_string_terms"]
        if candidate["string_hits"] != expected_hits:
            failures.append(
                f"candidate hit count {candidate['string_hits']} != expected {expected_hits}"
            )
        if candidate["string_misses"] != candidate["underlying_string_terms"]:
            failures.append(
                "candidate miss/underlying mismatch: "
                f"{candidate['string_misses']} != {candidate['underlying_string_terms']}"
            )
        if measured_fallbacks_per_mode <= 0 or not expected_output:
            failures.append("fixed fallback input produced no measurable ToolCall output")

        ratios = [float(row["ratio"]) for row in paired_rows]
        median_ratio = statistics.median(ratios)
        if median_ratio > POSITIVE_RATIO:
            failures.append(
                "task-local target cache missed fixed-input speedup threshold: "
                f"{median_ratio:.4f}x > {POSITIVE_RATIO:.2f}x"
            )

        result = {
            "ok": not failures,
            "experiments": EXPERIMENTS,
            "warmup_tasks_per_mode": WARMUP_TASKS,
            "measured_tasks_per_mode_per_experiment": MEASURED_TASKS,
            "fallbacks_per_task": FALLBACKS_PER_TASK,
            "measured_fallbacks_per_mode": measured_fallbacks_per_mode,
            "paired_operation_ratios": [round(value, 4) for value in ratios],
            "paired_operation_ratio_median": round(median_ratio, 4),
            "positive_ratio_threshold": POSITIVE_RATIO,
            "term_counters": {
                mode: {key: int(value) for key, value in sorted(values.items())}
                for mode, values in counters.items()
            },
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
                "cache_scope": "ContextVar dictionary created/reset once per simulated task",
                "cache_key": "exact string value; diagnostic output contains no keys",
                "cache_value": "frozenset terms; callers receive fresh set copies",
                "cross_task_retention": False,
            },
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result


def main() -> int:
    result = asyncio.run(main_async())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
