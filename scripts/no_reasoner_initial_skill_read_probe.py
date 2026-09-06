from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from ecomevo.runtime import EcomEvoEngine


DOMAIN = "merchant_review"
QUERY = "审核商家并核对主体、授权和历史风险"
MISSING = ("主体", "授权", "历史风险")
LIMIT = 6
ROUNDS = 5
READS_PER_ARM = 1200
EMPTY_RATIO_LIMIT = 0.85
SEEDED_RATIO_LIMIT = 0.90


def _normalize(skills: list[Any]) -> list[dict[str, Any]]:
    return [skill.as_dict() for skill in skills]


def _candidate_relevant_only(
    library,
    domain: str,
    *,
    query: str = "",
    missing: Iterable[str] = (),
    limit: int = 6,
):
    """Local prototype for a no-consumer initial read; production code is untouched."""
    library._decision_policy_snapshot.set(None)
    haystack = (str(query) + " " + " ".join(str(value) for value in missing)).lower()
    with library._conn() as connection:
        rows = connection.execute(
            "SELECT * FROM runtime_skills WHERE domain=? AND status='active' "
            "ORDER BY updated_at DESC LIMIT 100",
            (domain,),
        ).fetchall()
    candidates = [library._decode(row) for row in rows]
    scored: list[tuple[float, Any]] = []
    for skill in candidates:
        term_hits = sum(
            1 for term in skill.trigger_terms if term and term.lower() in haystack
        )
        score = (
            0.46 * skill.posterior_mean
            + 0.34 * skill.shadow_score
            + min(0.20, 0.05 * term_hits)
        )
        scored.append((score, skill))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [skill for _, skill in scored[: max(1, int(limit))]]


def _seed_active_skills(library, count: int) -> None:
    if count <= 0:
        return
    now = time.time()
    rows = []
    trigger_sets = [
        ["主体", "资质"],
        ["授权", "品牌"],
        ["历史风险", "异常"],
        ["商家", "经营范围"],
        ["主体", "注册地址"],
        ["授权", "历史风险"],
    ]
    for index in range(count):
        triggers = trigger_sets[index % len(trigger_sets)]
        rows.append(
            (
                f"probe-skill-{index}",
                DOMAIN,
                f"niche-{index}",
                f"Probe skill {index}",
                ("只读核对业务证据。" * (4 + index % 3)),
                json.dumps(["merchant.inspect", "evidence.search"], ensure_ascii=False),
                json.dumps(triggers, ensure_ascii=False),
                "active",
                0.55 + 0.02 * (index % 5),
                2.0 + index * 0.2,
                2.0 + (count - index) * 0.1,
                index,
                index // 2,
                index // 3,
                now + index * 0.001,
                now + index * 0.001,
                None,
            )
        )
    with library._conn() as connection:
        connection.executemany(
            "INSERT INTO runtime_skills("
            "skill_id,domain,niche,name,guidance,preferred_tools_json,trigger_terms_json,"
            "status,shadow_score,alpha,beta,uses,wins,losses,created_at,updated_at,source_patch_id"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def _trace_one(library, call) -> tuple[Any, list[str]]:
    original_conn = library._conn
    sql: list[str] = []

    def traced_conn():
        connection = original_conn()
        connection.set_trace_callback(
            lambda statement: sql.append(statement.strip())
            if statement.lstrip().upper().startswith(("BEGIN", "SELECT"))
            else None
        )
        return connection

    library._conn = traced_conn
    try:
        return call(), sql
    finally:
        library._conn = original_conn


def _time_arm(call, reads: int) -> float:
    started = time.perf_counter()
    for _ in range(reads):
        call()
    return time.perf_counter() - started


def _run_case(root: Path, *, active_skills: int, ratio_limit: float) -> dict[str, Any]:
    engine = EcomEvoEngine(root / f"skills-{active_skills}.db")
    library = engine.skills
    # Ensure #53's baseline policy SELECT reads an established steady-state row.
    expected_policy = library.policy(DOMAIN)
    _seed_active_skills(library, active_skills)

    def baseline():
        library._decision_policy_snapshot.set(None)
        return library.relevant(DOMAIN, query=QUERY, missing=MISSING, limit=LIMIT)

    def candidate():
        return _candidate_relevant_only(
            library,
            DOMAIN,
            query=QUERY,
            missing=MISSING,
            limit=LIMIT,
        )

    baseline_value, baseline_sql = _trace_one(library, baseline)
    baseline_snapshot = library._decision_policy_snapshot.get()
    library._decision_policy_snapshot.set(None)
    candidate_value, candidate_sql = _trace_one(library, candidate)
    candidate_snapshot = library._decision_policy_snapshot.get()

    # If a future caller unexpectedly asks for policy after the proposed skill-only path,
    # the existing public method must still return the exact durable payload via SQLite.
    fallback_policy = library.policy(DOMAIN)
    library._decision_policy_snapshot.set(None)

    failures: list[str] = []
    if _normalize(baseline_value) != _normalize(candidate_value):
        failures.append("candidate skill ordering/payload differs from baseline")
    if baseline_snapshot is None or baseline_snapshot[0] != DOMAIN:
        failures.append("baseline did not produce the expected one-shot policy snapshot")
    if candidate_snapshot is not None:
        failures.append("candidate unexpectedly retained a policy snapshot")
    if fallback_policy != expected_policy:
        failures.append("policy fallback after candidate differs from durable policy payload")

    baseline_selects = [row for row in baseline_sql if row.upper().startswith("SELECT")]
    candidate_selects = [row for row in candidate_sql if row.upper().startswith("SELECT")]
    if len(baseline_selects) != 2:
        failures.append(f"baseline SELECT count {len(baseline_selects)} != 2")
    if len(candidate_selects) != 1:
        failures.append(f"candidate SELECT count {len(candidate_selects)} != 1")

    # Warm both implementations before alternating paired measurements.
    _time_arm(baseline, 40)
    library._decision_policy_snapshot.set(None)
    _time_arm(candidate, 40)

    pairs: list[dict[str, Any]] = []
    for round_index in range(ROUNDS):
        order = ("baseline", "candidate") if round_index % 2 == 0 else ("candidate", "baseline")
        elapsed: dict[str, float] = {}
        for arm in order:
            library._decision_policy_snapshot.set(None)
            elapsed[arm] = _time_arm(baseline if arm == "baseline" else candidate, READS_PER_ARM)
        ratio = elapsed["candidate"] / elapsed["baseline"] if elapsed["baseline"] else 0.0
        pairs.append(
            {
                "round": round_index + 1,
                "order": list(order),
                "baseline_seconds": round(elapsed["baseline"], 6),
                "candidate_seconds": round(elapsed["candidate"], 6),
                "ratio": round(ratio, 4),
            }
        )

    ratios = [float(row["ratio"]) for row in pairs]
    median_ratio = statistics.median(ratios)
    if median_ratio > ratio_limit:
        failures.append(
            f"median ratio {median_ratio:.4f} > production threshold {ratio_limit:.2f}"
        )

    return {
        "active_skills": active_skills,
        "selected_skills": len(candidate_value),
        "semantic_equal": _normalize(baseline_value) == _normalize(candidate_value),
        "policy_fallback_equal": fallback_policy == expected_policy,
        "baseline_snapshot_present": baseline_snapshot is not None,
        "candidate_snapshot_absent": candidate_snapshot is None,
        "query_shape": {
            "baseline_selects": len(baseline_selects),
            "candidate_selects": len(candidate_selects),
            "baseline_sql": baseline_sql,
            "candidate_sql": candidate_sql,
        },
        "reads_per_arm_per_round": READS_PER_ARM,
        "rounds": ROUNDS,
        "pairs": pairs,
        "median_ratio": round(median_ratio, 4),
        "production_ratio_limit": ratio_limit,
        "failures": failures,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ecomevo-skill-only-read-") as tmp:
        root = Path(tmp)
        empty = _run_case(root, active_skills=0, ratio_limit=EMPTY_RATIO_LIMIT)
        seeded = _run_case(root, active_skills=6, ratio_limit=SEEDED_RATIO_LIMIT)
        failures = [
            f"empty: {failure}" for failure in empty["failures"]
        ] + [
            f"seeded: {failure}" for failure in seeded["failures"]
        ]
        result = {
            "ok": not failures,
            "candidate_scope": "no-reasoner initial skill read only",
            "reasoner_initial_path_unchanged": True,
            "cases": {"empty": empty, "seeded": seeded},
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
