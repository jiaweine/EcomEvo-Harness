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
TOOLS = ["merchant.inspect", "risk.scan", "evidence.search"]
QUERY = "审核商家主体、授权和历史风险"
MISSING = ["主体", "授权", "风险"]


def _policy_from_row(domain: str, row) -> dict[str, Any]:
    return {
        "domain": domain,
        "promotion_threshold": float(row["promotion_threshold"]),
        "retirement_threshold": float(row["retirement_threshold"]),
        "exploration": float(row["exploration"]),
        "updates": int(row["updates"]),
        "updated_at": float(row["updated_at"]),
    }


def _reliability_from_rows(routing, domain: str, tools: list[str], rows) -> dict[str, float]:
    indexed = {(str(row["domain"]), str(row["tool"])): row for row in rows}
    result: dict[str, float] = {}
    for tool in tools:
        global_row = indexed.get(("*", tool))
        local_row = indexed.get((domain, tool))
        global_mean = routing._beta_mean(global_row)
        local_mean = routing._beta_mean(local_row)
        local_uses = int(local_row["uses"]) if local_row else 0
        tau = local_uses / (local_uses + routing.RELIABILITY_SHRINKAGE)
        result[tool] = (1.0 - tau) * global_mean + tau * local_mean
    return result


def _rank_skills(skills, rows, *, query: str, missing: list[str], limit: int = 6):
    haystack = (str(query) + " " + " ".join(str(x) for x in missing)).lower()
    candidates = [skills._decode(row) for row in rows]
    scored = []
    for skill in candidates:
        term_hits = sum(1 for term in skill.trigger_terms if term and term.lower() in haystack)
        score = (
            0.46 * skill.posterior_mean
            + 0.34 * skill.shadow_score
            + min(0.20, 0.05 * term_hits)
        )
        scored.append((score, skill))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [skill for _, skill in scored[: max(1, int(limit))]]


def _prepared_from_rows(routing, domain: str, tools: list[str], exploration: float, policy_rows, reliability_rows):
    global_key = routing._key("*", "global")
    domain_key = routing._key(domain, "domain")
    indexed = {str(row["policy_key"]): row for row in policy_rows}
    global_p = routing._posterior_from_row(
        routing._decode_row(indexed.get(global_key), routing._default_row("*", "global"))
    )
    domain_p = routing._posterior_from_row(
        routing._decode_row(indexed.get(domain_key), routing._default_row(domain, "domain"))
    )
    n = int(domain_p["samples"])
    global_n = int(global_p["samples"])
    tau = n / (n + routing.DOMAIN_SHRINKAGE)
    residual = max(float(global_p["residual_ewma"]), float(domain_p["residual_ewma"]))
    confidence = max(0.30, min(1.0, 1.0 / (1.0 + 1.8 * residual)))
    if n < routing.SHADOW_MIN_SAMPLES:
        if global_n >= 48:
            activation = min(0.18, (global_n / (global_n + 180.0)) * confidence)
            mode = "global_transfer"
        else:
            activation = 0.0
            mode = "shadow"
    else:
        sample_factor = (n - routing.SHADOW_MIN_SAMPLES + 1.0) / (n + 28.0)
        activation = min(routing.MAX_ACTIVATION, sample_factor * confidence)
        mode = "adaptive"
    explore = max(0.0, min(1.0, float(exploration)))
    beta = (0.12 + 0.42 * explore) * (1.0 + min(1.0, residual))
    return {
        "global": global_p,
        "domain": domain_p,
        "tau": tau,
        "residual": residual,
        "activation": activation,
        "mode": mode,
        "beta": beta,
        "samples": n,
        "global_samples": global_n,
        "reliability": _reliability_from_rows(routing, domain, tools, reliability_rows),
        "prepare_ms": 0.0,
    }


def fused_read(engine: EcomEvoEngine):
    skills = engine.skills
    routing = engine.autonomy.policy._routing_source
    domain = DOMAIN
    tools = TOOLS
    global_key = routing._key("*", "global")
    domain_key = routing._key(domain, "domain")
    placeholders = ",".join("?" for _ in tools)

    with skills._conn() as connection:
        connection.execute("BEGIN")
        skill_rows = connection.execute(
            "SELECT * FROM runtime_skills WHERE domain=? AND status='active' "
            "ORDER BY updated_at DESC LIMIT 100",
            (domain,),
        ).fetchall()
        policy_row = connection.execute(
            "SELECT * FROM evolution_policy WHERE domain=?",
            (domain,),
        ).fetchone()
        routing_rows = connection.execute(
            "SELECT * FROM routing_policy WHERE policy_key IN (?,?)",
            (global_key, domain_key),
        ).fetchall()
        reliability_rows = connection.execute(
            f"SELECT domain,tool,alpha,beta,uses FROM routing_tool_stats "
            f"WHERE domain IN (?,?) AND tool IN ({placeholders})",  # nosec - placeholders only
            ["*", domain, *tools],
        ).fetchall()

    if policy_row is None:
        raise RuntimeError("probe expects seeded evolution policy")
    active = _rank_skills(skills, skill_rows, query=QUERY, missing=MISSING)
    policy = _policy_from_row(domain, policy_row)
    prepared = _prepared_from_rows(
        routing,
        domain,
        tools,
        policy["exploration"],
        routing_rows,
        reliability_rows,
    )
    return active, policy, prepared


def legacy_read(engine: EcomEvoEngine):
    active = engine.skills.relevant(DOMAIN, query=QUERY, missing=MISSING)
    policy = engine.skills.policy(DOMAIN)
    prepared = engine.autonomy.policy._routing_source.prepare_context(
        DOMAIN,
        tools=TOOLS,
        exploration=policy["exploration"],
    )
    return active, policy, prepared


def assert_equivalent(legacy, fused) -> float:
    legacy_skills, legacy_policy, legacy_prepared = legacy
    fused_skills, fused_policy, fused_prepared = fused
    if [row.skill_id for row in legacy_skills] != [row.skill_id for row in fused_skills]:
        raise AssertionError("skill ranking changed")
    if legacy_policy != fused_policy:
        raise AssertionError("evolution policy changed")
    if legacy_prepared["mode"] != fused_prepared["mode"]:
        raise AssertionError("routing mode changed")
    if legacy_prepared["reliability"] != fused_prepared["reliability"]:
        raise AssertionError("tool reliability changed")

    largest = 0.0
    for key in ("tau", "residual", "activation", "beta"):
        largest = max(largest, abs(float(legacy_prepared[key]) - float(fused_prepared[key])))
    vector = [1.0, 0.7, 0.2, 0.6, 1.0, 0.0, 0.5, 0.8, 0.3, 0.0, 0.9, 1.0]
    routing = None
    return largest


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
        rows.append({"tool": tool, "vector": vector, "reward": 0.2 + index * 0.1, "ok": True, "meta": {}})
    for _ in range(16):
        routing.apply_batch(DOMAIN, phase="probe", rows=rows)


def timed(call, iterations: int):
    started = time.perf_counter()
    result = None
    for _ in range(iterations):
        result = call()
    return time.perf_counter() - started, result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe one-snapshot decision read fusion")
    parser.add_argument("--iterations", type=int, default=600)
    parser.add_argument("--experiments", type=int, default=5)
    args = parser.parse_args()
    if args.iterations < 50 or args.iterations > 5000:
        raise SystemExit("iterations must be between 50 and 5000")
    if args.experiments < 3 or args.experiments > 9:
        raise SystemExit("experiments must be between 3 and 9")

    with tempfile.TemporaryDirectory(prefix="ecomevo-decision-read-fusion-") as tmp:
        engine = EcomEvoEngine(Path(tmp) / "fusion.db")
        seed(engine)

        legacy = legacy_read(engine)
        fused = fused_read(engine)
        max_delta = assert_equivalent(legacy, fused)
        routing = engine.autonomy.policy._routing_source
        vector = [1.0, 0.7, 0.2, 0.6, 1.0, 0.0, 0.5, 0.8, 0.3, 0.0, 0.9, 1.0]
        legacy_score = routing.score_prepared(vector, legacy[2])
        fused_score = routing.score_prepared(vector, fused[2])
        for key in ("score", "prior", "posterior", "uncertainty", "activation", "residual"):
            max_delta = max(max_delta, abs(float(legacy_score[key]) - float(fused_score[key])))

        timed(lambda: legacy_read(engine), 20)
        timed(lambda: fused_read(engine), 20)
        legacy_times = []
        fused_times = []
        for experiment in range(args.experiments):
            order = ("legacy", "fused") if experiment % 2 == 0 else ("fused", "legacy")
            for mode in order:
                if mode == "legacy":
                    elapsed, _ = timed(lambda: legacy_read(engine), args.iterations)
                    legacy_times.append(elapsed)
                else:
                    elapsed, _ = timed(lambda: fused_read(engine), args.iterations)
                    fused_times.append(elapsed)

        legacy_median = statistics.median(legacy_times)
        fused_median = statistics.median(fused_times)
        ratio = fused_median / max(1e-12, legacy_median)
        failures = []
        if max_delta > 1e-10:
            failures.append(f"numeric/semantic delta too large: {max_delta:.3e}")
        # This is a diagnostic, not a merge gate. A material reduction is required before
        # promoting the design into production; no threshold will be relaxed later.
        if ratio > 0.85:
            failures.append(f"read fusion is not material enough: ratio={ratio:.4f} > 0.85")

        result = {
            "ok": not failures,
            "iterations": args.iterations,
            "experiments": args.experiments,
            "legacy_physical_connections_per_recovery": 2,
            "fused_physical_connections_per_recovery": 1,
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
