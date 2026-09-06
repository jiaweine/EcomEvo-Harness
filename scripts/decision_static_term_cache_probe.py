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
WARMUP_CALLS = 24
MEASURED_CALLS = 320
POSITIVE_RATIO = 0.95
CACHE_LIMIT = 128


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
            "审核商家并核对主体、授权和历史风险。static-term cache probe。",
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
    with tempfile.TemporaryDirectory(prefix="ecomevo-static-term-cache-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "probe.db")
        policy = engine.autonomy.policy
        captured_args, captured_kwargs = await _capture_fallback(engine)

        goal = captured_args[0]
        belief = captured_args[1]
        assets = captured_args[2]
        remaining_budget = float(captured_kwargs["remaining_budget"])
        previous = captured_kwargs["previous"]
        skills = captured_kwargs["skills"]

        mode_var: ContextVar[str] = ContextVar("static-term-cache-mode", default="candidate")
        count_var: ContextVar[bool] = ContextVar("static-term-cache-count", default=False)
        original_terms = policy._terms

        # Diagnostic candidate: only set-valued tool metadata is cached. The key is the
        # exact text the legacy `_terms` implementation would join in this process, so a
        # different plugin metadata value/order naturally misses. Cached values are
        # immutable; every caller receives a fresh mutable set copy.
        cache: dict[str, frozenset[str]] = {}
        cache_order: list[str] = []
        counters: dict[str, Counter[str]] = {
            "baseline": Counter(),
            "candidate": Counter(),
        }

        def cached_set_terms(value: set[Any]) -> set[str]:
            mode = mode_var.get()
            text_key = " ".join(str(item) for item in value)
            if count_var.get():
                counters[mode]["set_lookups"] += 1
            cached = cache.get(text_key)
            if cached is not None:
                if count_var.get():
                    counters[mode]["set_hits"] += 1
                return set(cached)
            if count_var.get():
                counters[mode]["set_misses"] += 1
                counters[mode]["underlying_set_terms"] += 1
            result = frozenset(original_terms(value))
            cache[text_key] = result
            cache_order.append(text_key)
            if len(cache_order) > CACHE_LIMIT:
                evicted = cache_order.pop(0)
                cache.pop(evicted, None)
            return set(result)

        def wrapped_terms(value: Any):
            mode = mode_var.get()
            if isinstance(value, set):
                if mode == "candidate":
                    return cached_set_terms(value)
                if count_var.get():
                    counters[mode]["set_lookups"] += 1
                    counters[mode]["set_misses"] += 1
                    counters[mode]["underlying_set_terms"] += 1
                return original_terms(value)
            if count_var.get():
                counters[mode]["nonset_terms"] += 1
            return original_terms(value)

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

        expected_output: list[dict[str, Any]] | None = None
        per_call_ms: dict[str, list[float]] = {"baseline": [], "candidate": []}
        paired_rows: list[dict[str, Any]] = []

        def run_arm(mode: str, *, measured: bool) -> tuple[float, int]:
            nonlocal expected_output
            mode_token = mode_var.set(mode)
            count_token = count_var.set(measured)
            calls = MEASURED_CALLS if measured else WARMUP_CALLS
            elapsed_total = 0.0
            mismatches = 0
            try:
                for _ in range(calls):
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
                        per_call_ms[mode].append(elapsed_ms)
            finally:
                count_var.reset(count_token)
                mode_var.reset(mode_token)
            return elapsed_total, mismatches

        # Warm the candidate as a long-lived process would be after its first metadata use.
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
                    f"experiment {experiment} changed normalized ToolCall output: "
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

        measured_per_mode = EXPERIMENTS * MEASURED_CALLS
        baseline = counters["baseline"]
        candidate = counters["candidate"]
        if baseline["set_lookups"] != measured_per_mode:
            failures.append(
                f"baseline set lookups: {baseline['set_lookups']} != {measured_per_mode}"
            )
        if candidate["set_lookups"] != measured_per_mode:
            failures.append(
                f"candidate set lookups: {candidate['set_lookups']} != {measured_per_mode}"
            )
        if baseline["underlying_set_terms"] != measured_per_mode:
            failures.append(
                "baseline underlying set computations changed: "
                f"{baseline['underlying_set_terms']} != {measured_per_mode}"
            )
        if candidate["underlying_set_terms"] != 0:
            failures.append(
                "warmed candidate still recomputed set terms during measurement: "
                f"{candidate['underlying_set_terms']}"
            )
        if candidate["set_hits"] != measured_per_mode:
            failures.append(
                f"candidate cache hits: {candidate['set_hits']} != {measured_per_mode}"
            )
        if candidate["nonset_terms"] != baseline["nonset_terms"]:
            failures.append(
                "candidate changed request/target term work: "
                f"{candidate['nonset_terms']} != {baseline['nonset_terms']}"
            )
        if not expected_output:
            failures.append("fixed fallback input produced no ToolCall output")

        ratios = [float(row["ratio"]) for row in paired_rows]
        median_ratio = statistics.median(ratios)
        if median_ratio > POSITIVE_RATIO:
            failures.append(
                "static metadata cache missed fixed-input speedup threshold: "
                f"{median_ratio:.4f}x > {POSITIVE_RATIO:.2f}x"
            )

        result = {
            "ok": not failures,
            "experiments": EXPERIMENTS,
            "warmup_calls_per_mode": WARMUP_CALLS,
            "measured_calls_per_mode_per_experiment": MEASURED_CALLS,
            "cache_limit": CACHE_LIMIT,
            "cache_entries": len(cache),
            "paired_operation_ratios": [round(value, 4) for value in ratios],
            "paired_operation_ratio_median": round(median_ratio, 4),
            "positive_ratio_threshold": POSITIVE_RATIO,
            "term_counters": {
                mode: {key: int(value) for key, value in sorted(values.items())}
                for mode, values in counters.items()
            },
            "per_call_latency_ms": {
                mode: {
                    "p50": round(_percentile(values, 0.50), 4),
                    "p95": round(_percentile(values, 0.95), 4),
                    "mean": round(sum(values) / max(1, len(values)), 4),
                }
                for mode, values in per_call_ms.items()
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
                "request_text_cached": False,
                "cached_input_types": ["set"],
                "note": "Diagnostic cache contains only exact joined set-valued tool metadata.",
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
