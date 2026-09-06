from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


TASKS = 120


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _canonical(value: Any) -> tuple[str, Any, int]:
    if isinstance(value, set):
        rows = sorted(str(item) for item in value)
        return "tool_static_set", rows, sum(len(item) for item in rows)
    if isinstance(value, (list, tuple)):
        rows = [str(item) for item in value]
        return "sequence", rows, sum(len(item) for item in rows)
    text = str(value or "")
    return "target_text" if isinstance(value, str) else "other", text, len(text)


def _fingerprint(value: Any) -> tuple[str, str, int]:
    category, normalized, input_chars = _canonical(value)
    body = json.dumps(
        {"category": category, "value": normalized},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return category, hashlib.sha256(body.encode()).hexdigest(), input_chars


async def main_async() -> dict[str, Any]:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-term-reuse-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "probe.db")
        policy = engine.autonomy.policy

        task_var: ContextVar[int | None] = ContextVar("decision-term-task", default=None)
        phase_var: ContextVar[str] = ContextVar("decision-term-phase", default="unknown")
        rows: list[dict[str, Any]] = []
        rank_shapes: Counter[str] = Counter()
        sanitize_counts: Counter[str] = Counter()

        original_terms = policy._terms
        original_sanitize = policy.sanitize
        original_rank = policy._rank_candidates

        def wrapped_terms(value: Any):
            task_id = task_var.get()
            phase = phase_var.get()
            category, digest, input_chars = _fingerprint(value)
            started = time.perf_counter()
            result = original_terms(value)
            rows.append(
                {
                    "task": task_id,
                    "phase": phase,
                    "category": category,
                    "digest": digest,
                    "input_chars": input_chars,
                    "term_count": len(result),
                    "elapsed_ms": (time.perf_counter() - started) * 1000.0,
                }
            )
            return result

        def wrapped_sanitize(*args, **kwargs):
            phase = str(kwargs.get("phase") or "unknown")
            sanitize_counts[phase] += 1
            token = phase_var.set(phase)
            try:
                return original_sanitize(*args, **kwargs)
            finally:
                phase_var.reset(token)

        def wrapped_rank(candidates, *args, **kwargs):
            phase = phase_var.get()
            rank_shapes[f"{phase}:{len(candidates)}"] += 1
            return original_rank(candidates, *args, **kwargs)

        policy._terms = wrapped_terms
        policy.sanitize = wrapped_sanitize
        policy._rank_candidates = wrapped_rank

        async def one(index: int) -> None:
            token = task_var.set(index)
            try:
                summary = await engine.run(
                    f"审核商家并核对主体、授权和历史风险。term-value probe {index}。",
                    [],
                    domain_hint="merchant_review",
                )
                if not summary.event_chain_valid:
                    failures.append(f"{index}: invalid event chain")
            finally:
                task_var.reset(token)

        started = time.perf_counter()
        await asyncio.gather(*(one(index) for index in range(TASKS)))
        wall_seconds = time.perf_counter() - started

        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_digest: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_task_digest: Counter[tuple[int | None, str]] = Counter()
        for row in rows:
            by_category[str(row["category"])].append(row)
            by_digest[str(row["digest"])].append(row)
            by_task_digest[(row["task"], str(row["digest"]))] += 1

        category_report: dict[str, Any] = {}
        for category, items in sorted(by_category.items()):
            durations = [float(row["elapsed_ms"]) for row in items]
            digests = {str(row["digest"]) for row in items}
            task_sets = {
                digest: {row["task"] for row in by_digest[digest]}
                for digest in digests
            }
            within_task_repeat_calls = sum(
                max(0, count - 1)
                for (task_id, digest), count in by_task_digest.items()
                if digest in digests and task_id is not None
            )
            category_report[category] = {
                "calls": len(items),
                "unique_fingerprints": len(digests),
                "repeat_calls_beyond_first_global": len(items) - len(digests),
                "unique_ratio": round(len(digests) / max(1, len(items)), 4),
                "within_task_repeat_calls": within_task_repeat_calls,
                "fingerprints_seen_in_multiple_tasks": sum(
                    1 for digest in digests if len(task_sets[digest]) > 1
                ),
                "max_tasks_per_fingerprint": max(
                    (len(task_sets[digest]) for digest in digests), default=0
                ),
                "total_ms": round(sum(durations), 3),
                "p50_ms": round(_percentile(durations, 0.50), 4),
                "p95_ms": round(_percentile(durations, 0.95), 4),
            }

        top_fingerprints = []
        for digest, items in sorted(
            by_digest.items(),
            key=lambda pair: (len(pair[1]), sum(float(row["elapsed_ms"]) for row in pair[1])),
            reverse=True,
        )[:12]:
            durations = [float(row["elapsed_ms"]) for row in items]
            top_fingerprints.append(
                {
                    "fingerprint": digest[:16],
                    "category": str(items[0]["category"]),
                    "calls": len(items),
                    "tasks": len({row["task"] for row in items}),
                    "phase_counts": dict(Counter(str(row["phase"]) for row in items)),
                    "input_chars": int(items[0]["input_chars"]),
                    "term_count": int(items[0]["term_count"]),
                    "total_ms": round(sum(durations), 3),
                    "p50_ms": round(_percentile(durations, 0.50), 4),
                }
            )

        result = {
            "ok": not failures,
            "tasks": TASKS,
            "wall_seconds": round(wall_seconds, 4),
            "sanitize_phase_counts": dict(sanitize_counts),
            "rank_candidate_shapes": dict(rank_shapes),
            "terms_calls": len(rows),
            "terms_total_ms": round(sum(float(row["elapsed_ms"]) for row in rows), 3),
            "category_report": category_report,
            "top_fingerprints": top_fingerprints,
            "privacy": {
                "raw_values_emitted": False,
                "fingerprint": "sha256(category + canonical value)",
                "note": "Only digest/category/length/count/timing metrics are emitted.",
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
