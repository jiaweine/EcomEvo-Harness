from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine


DOMAIN = "merchant_review"
QUERY = "审核商家主体、授权和历史风险"
MISSING = ["主体", "授权", "风险"]
TOOLS = ["merchant.inspect", "risk.scan", "evidence.search"]


def seed(engine: EcomEvoEngine) -> None:
    engine.skills.policy(DOMAIN)
    engine.skills.upsert_candidate(
        domain=DOMAIN,
        name="merchant-auth",
        guidance="核对主体授权",
        preferred_tools=["merchant.inspect"],
        trigger_terms=["主体", "授权"],
        shadow_score=0.95,
        promote=True,
    )
    engine.skills.upsert_candidate(
        domain=DOMAIN,
        name="merchant-risk",
        guidance="核对历史风险",
        preferred_tools=["risk.scan"],
        trigger_terms=["风险"],
        shadow_score=0.88,
        promote=True,
    )
    routing = engine.autonomy.policy._routing_source
    rows = []
    for index, tool in enumerate(TOOLS):
        vector = [0.0] * routing.dim
        vector[0] = 1.0
        vector[1] = 0.35 + index * 0.1
        vector[7] = 0.5
        rows.append(
            {
                "tool": tool,
                "vector": vector,
                "reward": 0.2 + index * 0.1,
                "ok": True,
                "meta": {"seed": True},
            }
        )
    for _ in range(16):
        routing.apply_batch(DOMAIN, phase="gate", rows=rows)


def legacy_read(engine: EcomEvoEngine):
    skills = engine.skills
    policy = engine.autonomy.policy
    routing = policy._routing_source
    active = skills.relevant(DOMAIN, query=QUERY, missing=MISSING)
    evolution = skills.policy(DOMAIN)
    prepared = routing.prepare_context(
        DOMAIN,
        tools=list(engine.tools.tools),
        exploration=float(evolution["exploration"]),
    )
    return active, evolution, prepared


def fused_read(engine: EcomEvoEngine):
    policy = engine.autonomy.policy
    active = policy.prepare_round_skills(
        DOMAIN,
        query=QUERY,
        missing=MISSING,
    )
    if active is None:
        raise AssertionError("default runtime did not expose fused decision read")
    current = policy._decision_round.get()
    if current is None or current.skill_policy is None or current.routing_prepared is None:
        raise AssertionError("fused decision read did not preload round context")
    return active, current.skill_policy, current.routing_prepared


def routing_shape(prepared: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in prepared.items() if key != "prepare_ms"}


def assert_equivalent(legacy, fused, routing) -> float:
    legacy_skills, legacy_policy, legacy_prepared = legacy
    fused_skills, fused_policy, fused_prepared = fused
    if [row.as_dict() for row in legacy_skills] != [row.as_dict() for row in fused_skills]:
        raise AssertionError("active skill selection changed")
    if legacy_policy != fused_policy:
        raise AssertionError("evolution policy changed")
    if routing_shape(legacy_prepared) != routing_shape(fused_prepared):
        raise AssertionError("routing snapshot changed")

    vector = [1.0, 0.7, 0.2, 0.6, 1.0, 0.0, 0.5, 0.8, 0.3, 0.0, 0.9, 1.0]
    legacy_score = routing.score_prepared(vector, legacy_prepared)
    fused_score = routing.score_prepared(vector, fused_prepared)
    largest = 0.0
    for key in ("score", "prior", "posterior", "uncertainty", "activation", "residual"):
        largest = max(largest, abs(float(legacy_score[key]) - float(fused_score[key])))
    return largest


def count_connections(engine: EcomEvoEngine, call) -> tuple[int, int]:
    skills = engine.skills
    routing = engine.autonomy.policy._routing_source
    skill_calls = 0
    routing_calls = 0
    skill_conn = skills._conn
    routing_conn = routing._conn

    def counted_skill_conn():
        nonlocal skill_calls
        skill_calls += 1
        return skill_conn()

    def counted_routing_conn():
        nonlocal routing_calls
        routing_calls += 1
        return routing_conn()

    skills._conn = counted_skill_conn
    routing._conn = counted_routing_conn
    try:
        call()
    finally:
        skills._conn = skill_conn
        routing._conn = routing_conn
    return skill_calls, routing_calls


def timed(call, iterations: int) -> float:
    started = time.perf_counter()
    for _ in range(iterations):
        call()
    return time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Production decision read fusion A/B gate")
    parser.add_argument("--iterations", type=int, default=600)
    parser.add_argument("--experiments", type=int, default=5)
    args = parser.parse_args()
    if not 50 <= args.iterations <= 5000:
        raise SystemExit("iterations must be between 50 and 5000")
    if not 3 <= args.experiments <= 9:
        raise SystemExit("experiments must be between 3 and 9")

    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-read-gate-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "fusion.db")
        seed(engine)
        routing = engine.autonomy.policy._routing_source

        legacy = legacy_read(engine)
        fused = fused_read(engine)
        max_delta = assert_equivalent(legacy, fused, routing)

        legacy_connections = count_connections(engine, lambda: legacy_read(engine))
        fused_connections = count_connections(engine, lambda: fused_read(engine))

        timed(lambda: legacy_read(engine), 20)
        timed(lambda: fused_read(engine), 20)
        legacy_times: list[float] = []
        fused_times: list[float] = []
        for experiment in range(args.experiments):
            order = ("legacy", "fused") if experiment % 2 == 0 else ("fused", "legacy")
            for mode in order:
                if mode == "legacy":
                    legacy_times.append(timed(lambda: legacy_read(engine), args.iterations))
                else:
                    fused_times.append(timed(lambda: fused_read(engine), args.iterations))

        legacy_median = statistics.median(legacy_times)
        fused_median = statistics.median(fused_times)
        ratio = fused_median / max(1e-12, legacy_median)
        failures = []
        if legacy_connections != (1, 1):
            failures.append(f"legacy connection shape changed: {legacy_connections}")
        if fused_connections != (1, 0):
            failures.append(f"fused connection shape changed: {fused_connections}")
        if max_delta > 1e-10:
            failures.append(f"numeric delta too large: {max_delta:.3e}")
        # Pre-declared from the diagnostic prototype. Do not relax after observing CI.
        if ratio > 0.85:
            failures.append(f"fused wall ratio regressed: {ratio:.4f} > 0.85")

        result = {
            "ok": not failures,
            "iterations": args.iterations,
            "experiments": args.experiments,
            "legacy_connections": {"skills": legacy_connections[0], "routing": legacy_connections[1]},
            "fused_connections": {"skills": fused_connections[0], "routing": fused_connections[1]},
            "legacy_median_seconds": round(legacy_median, 6),
            "fused_median_seconds": round(fused_median, 6),
            "fused_to_legacy_ratio": round(ratio, 4),
            "max_numeric_delta": max_delta,
            "failures": failures,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
