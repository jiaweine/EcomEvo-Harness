from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.factorized_routing import FactorizedAdaptiveRoutingStore


DOMAIN = "aftersales"
READS = 1200
ROUNDS = 5
RATIO_LIMIT = 0.80


def project(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": snapshot["domain"],
        "samples": int(snapshot.get("samples", 0)),
        "reward_ewma": float(snapshot.get("reward_ewma", 0.0)),
        "residual_ewma": float(snapshot.get("residual_ewma", 0.0)),
    }


def seed(store: FactorizedAdaptiveRoutingStore) -> None:
    for index in range(36):
        vector = [0.0] * store.dim
        vector[0] = 1.0
        vector[1] = 0.35 + (index % 5) * 0.08
        vector[4] = 1.0 / (1.0 + index % 4)
        vector[7] = 0.55 + (index % 3) * 0.07
        vector[8] = 0.2
        vector[10] = 0.5
        vector[11] = 1.0
        store.apply_batch(
            DOMAIN,
            phase="recovery",
            rows=[
                {
                    "tool": "evidence.search",
                    "vector": vector,
                    "reward": 0.2 + (index % 7) * 0.08,
                    "ok": index % 6 != 0,
                    "meta": {"gate": True},
                }
            ],
        )


def operation_shape(store: FactorizedAdaptiveRoutingStore) -> dict[str, Any]:
    decode_calls = 0
    posterior_calls = 0
    original_decode = store._decode_row
    original_posterior = store._posterior_from_row

    def counted_decode(row, fallback):
        nonlocal decode_calls
        decode_calls += 1
        return original_decode(row, fallback)

    def counted_posterior(row):
        nonlocal posterior_calls
        posterior_calls += 1
        return original_posterior(row)

    store._decode_row = counted_decode  # type: ignore[method-assign]
    store._posterior_from_row = counted_posterior  # type: ignore[method-assign]
    try:
        baseline = project(store.snapshot(DOMAIN))
        baseline_counts = {"decode_calls": decode_calls, "posterior_calls": posterior_calls}
        decode_calls = 0
        posterior_calls = 0
        candidate = store.state_summary(DOMAIN)
        candidate_counts = {"decode_calls": decode_calls, "posterior_calls": posterior_calls}
    finally:
        store._decode_row = original_decode  # type: ignore[method-assign]
        store._posterior_from_row = original_posterior  # type: ignore[method-assign]

    return {
        "equal": baseline == candidate,
        "baseline": baseline_counts,
        "candidate": candidate_counts,
    }


def benchmark(store: FactorizedAdaptiveRoutingStore) -> tuple[list[dict[str, Any]], float]:
    paired: list[dict[str, Any]] = []
    sink = 0
    for round_index in range(ROUNDS):
        order = ["baseline", "candidate"] if round_index % 2 == 0 else ["candidate", "baseline"]
        timings: dict[str, float] = {}
        for arm in order:
            started = time.perf_counter()
            for _ in range(READS):
                value = project(store.snapshot(DOMAIN)) if arm == "baseline" else store.state_summary(DOMAIN)
                sink += int(value["samples"])
            timings[arm] = time.perf_counter() - started
        paired.append(
            {
                "round": round_index + 1,
                "baseline_seconds": round(timings["baseline"], 6),
                "candidate_seconds": round(timings["candidate"], 6),
                "ratio": round(timings["candidate"] / max(timings["baseline"], 1e-12), 4),
            }
        )
    if sink <= 0:
        raise AssertionError("benchmark sink was not exercised")
    return paired, statistics.median(row["ratio"] for row in paired)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ecomevo-routing-state-summary-") as tmp:
        store = FactorizedAdaptiveRoutingStore(Path(tmp) / "routing.db")
        empty_equal = store.state_summary("content_audit") == project(store.snapshot("content_audit"))
        seed(store)
        learned_equal = store.state_summary(DOMAIN) == project(store.snapshot(DOMAIN))
        shape = operation_shape(store)
        paired, median_ratio = benchmark(store)

    failures: list[str] = []
    if not empty_equal:
        failures.append("empty-domain state summary differs from snapshot projection")
    if not learned_equal:
        failures.append("learned-domain state summary differs from snapshot projection")
    if not shape["equal"]:
        failures.append("operation-shape comparison changed projected values")
    if shape["baseline"] != {"decode_calls": 1, "posterior_calls": 1}:
        failures.append(f"unexpected baseline operation shape: {shape['baseline']}")
    if shape["candidate"] != {"decode_calls": 0, "posterior_calls": 0}:
        failures.append(f"candidate still decodes/factorizes posterior: {shape['candidate']}")
    if median_ratio > RATIO_LIMIT:
        failures.append(f"median ratio {median_ratio:.4f} > {RATIO_LIMIT:.2f}")

    report = {
        "ok": not failures,
        "semantic_equivalence": {
            "empty_domain": empty_equal,
            "learned_domain": learned_equal,
        },
        "operation_shape": shape,
        "benchmark": {
            "reads_per_arm_per_round": READS,
            "rounds": ROUNDS,
            "paired": paired,
            "median_ratio": round(median_ratio, 4),
            "ratio_limit": RATIO_LIMIT,
        },
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
