from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime.adaptive_routing import AdaptiveRoutingStore
from ecomevo.runtime.factorized_routing import FactorizedAdaptiveRoutingStore


def precision_row(store: AdaptiveRoutingStore, *, domain: str, scope: str, offset: float):
    matrix = store._prior_a()
    vector = store._prior_b()
    for step in range(1, 33):
        features = [
            (((step + 3 * index) % 11) - 5) / 7.0 + offset * (index + 1) / 30.0
            for index in range(store.dim)
        ]
        reward = (((step * 7) % 13) - 6) / 8.0
        for i, left in enumerate(features):
            vector[i] += reward * left
            for j, right in enumerate(features):
                matrix[i][j] += left * right
    return {
        "policy_key": store._key(domain, scope),
        "domain": domain,
        "scope": scope,
        "a": matrix,
        "b": vector,
        "samples": 128,
        "reward_ewma": 0.18,
        "residual_ewma": 0.23,
        "updated_at": 1.0,
    }


def prepared(store, global_row, domain_row):
    return {
        "global": store._posterior_from_row(global_row),
        "domain": store._posterior_from_row(domain_row),
        "tau": 0.67,
        "residual": 0.23,
        "activation": 0.84,
        "mode": "adaptive",
        "beta": 0.39,
        "samples": 128,
        "global_samples": 224,
        "reliability": {},
        "prepare_ms": 0.0,
    }


def run_mode(store, global_row, domain_row, vectors, iterations: int) -> tuple[float, dict[str, Any]]:
    final = None
    started = time.perf_counter()
    for _ in range(iterations):
        context = prepared(store, global_row, domain_row)
        final = [store.score_prepared(vector, context) for vector in vectors]
    elapsed = time.perf_counter() - started
    assert final is not None
    return elapsed, {"scores": final, "iterations": iterations}


def max_result_delta(left: dict[str, Any], right: dict[str, Any]) -> float:
    largest = 0.0
    for left_score, right_score in zip(left["scores"], right["scores"]):
        for key in ("score", "prior", "posterior", "uncertainty", "activation", "residual"):
            largest = max(largest, abs(float(left_score[key]) - float(right_score[key])))
    return largest


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B gate for factorized routing posterior scoring")
    parser.add_argument("--iterations", type=int, default=480)
    parser.add_argument("--experiments", type=int, default=5)
    args = parser.parse_args()
    if args.iterations < 32 or args.iterations > 5000:
        raise SystemExit("iterations must be between 32 and 5000")
    if args.experiments < 3 or args.experiments > 9:
        raise SystemExit("experiments must be between 3 and 9")

    with tempfile.TemporaryDirectory(prefix="ecomevo-routing-factor-") as tmp:
        root = Path(tmp)
        legacy = AdaptiveRoutingStore(root / "legacy.db")
        factorized = FactorizedAdaptiveRoutingStore(root / "factorized.db")
        global_row = precision_row(legacy, domain="*", scope="global", offset=0.07)
        domain_row = precision_row(legacy, domain="merchant_review", scope="domain", offset=0.19)
        vectors = [
            [1.0, 0.7, 0.2, 0.6, 1.0, 0.0, 0.5, 0.8, 0.3, 0.0, 0.9, 1.0],
            [1.0, 0.1, 1.0, 0.2, 0.5, 1.0, 0.7, 0.4, 0.8, 0.3, 0.2, 1.0],
            [1.0, 0.5, 0.8, 0.4, 0.7, 0.2, 0.9, 0.6, 0.1, 0.5, 1.0, 1.0],
            legacy.abstain_vector(gap_pressure=0.8, recovery_context=1.0),
        ]

        # Warm both implementations before timing.
        run_mode(legacy, global_row, domain_row, vectors, 32)
        run_mode(factorized, global_row, domain_row, vectors, 32)

        legacy_times: list[float] = []
        factorized_times: list[float] = []
        max_delta = 0.0
        for experiment in range(args.experiments):
            order = (
                (("legacy", legacy), ("factorized", factorized))
                if experiment % 2 == 0
                else (("factorized", factorized), ("legacy", legacy))
            )
            results: dict[str, dict[str, Any]] = {}
            for name, store in order:
                elapsed, result = run_mode(
                    store,
                    global_row,
                    domain_row,
                    vectors,
                    args.iterations,
                )
                results[name] = result
                if name == "legacy":
                    legacy_times.append(elapsed)
                else:
                    factorized_times.append(elapsed)
            max_delta = max(max_delta, max_result_delta(results["legacy"], results["factorized"]))

        legacy_median = statistics.median(legacy_times)
        factorized_median = statistics.median(factorized_times)
        ratio = factorized_median / max(1e-12, legacy_median)
        failures = []
        if max_delta > 1e-10:
            failures.append(f"numeric delta too large: {max_delta:.3e} > 1e-10")
        # Pre-declared same-process CPU gate. Private development measurements are much
        # lower; 0.70 leaves generous hosted-runner headroom while still requiring a
        # material algorithmic win over full Gauss-Jordan inversion.
        if ratio > 0.70:
            failures.append(f"factorized CPU ratio regressed: {ratio:.4f} > 0.70")

        result = {
            "ok": not failures,
            "iterations": args.iterations,
            "experiments": args.experiments,
            "vectors_per_iteration": len(vectors),
            "legacy_median_seconds": round(legacy_median, 6),
            "factorized_median_seconds": round(factorized_median, 6),
            "factorized_to_legacy_ratio": round(ratio, 4),
            "max_numeric_delta": max_delta,
            "failures": failures,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
