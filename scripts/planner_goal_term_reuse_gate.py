from __future__ import annotations

import json
import statistics
import time
from typing import Any

from ecomevo.runtime import planner as planner_module
from ecomevo.runtime.planner import AdaptivePlanner


TASKS = 800
ROUNDS = 5
RATIO_LIMIT = 0.95


def normalize(calls) -> list[dict[str, Any]]:
    return [
        {
            "tool": call.tool,
            "purpose": call.purpose,
            "args": call.args,
            "estimated_cost": call.estimated_cost,
            "parallel_group": call.parallel_group,
        }
        for call in calls
    ]


def rows(planner: AdaptivePlanner, tasks: int):
    out = []
    texts = [
        "退款订单争议需要核对物流签收、卖家举证与适用规则",
        "售后退款争议需要核对买家证据、履约状态与平台规则",
        "订单拒收争议需要核对物流轨迹、签收记录与赔付规则",
        "售后判责需要核对订单事实、争议证据和风险信号",
    ]
    for index in range(tasks):
        goal = planner.parse_goal(texts[index % len(texts)], [], domain_hint="aftersales")
        belief = planner.initial_belief(goal, [])
        belief.facts["conversation_context_terms"] = ["订单", "物流", "争议"]
        belief.facts["memory_watch_terms"] = ["拒收", "签收"]
        belief.missing_evidence = ["订单/履约信息", "争议证据", "适用规则"]
        out.append((goal, belief))
    return out


def make_baseline() -> AdaptivePlanner:
    planner = AdaptivePlanner()

    def uncached(goal, *, recovery: bool):
        return planner_module._query_terms(goal.primary, limit=24)

    planner._goal_terms = uncached  # type: ignore[method-assign]
    return planner


def verify_semantics() -> dict[str, Any]:
    baseline = make_baseline()
    candidate = AdaptivePlanner()
    baseline_rows = rows(baseline, 64)
    candidate_rows = rows(candidate, 64)
    mismatches = []
    fresh_identity = True
    for index, ((base_goal, base_belief), (cand_goal, cand_belief)) in enumerate(
        zip(baseline_rows, candidate_rows, strict=True)
    ):
        base_calls = [
            baseline.plan(base_goal, base_belief, [], recovery=False),
            baseline.plan(base_goal, base_belief, [], recovery=True),
            baseline.plan(base_goal, base_belief, [], recovery=True),
        ]
        cand_calls = [
            candidate.plan(cand_goal, cand_belief, [], recovery=False),
            candidate.plan(cand_goal, cand_belief, [], recovery=True),
            candidate.plan(cand_goal, cand_belief, [], recovery=True),
        ]
        if [normalize(value) for value in base_calls] != [normalize(value) for value in cand_calls]:
            mismatches.append(index)
        if [call.call_id for call in cand_calls[1]] == [call.call_id for call in cand_calls[2]]:
            fresh_identity = False
    return {
        "tasks": 64,
        "normalized_output_mismatches": mismatches,
        "fresh_recovery_call_identity": fresh_identity,
    }


def main() -> int:
    original_query_terms = planner_module._query_terms
    active_arm = {"name": None}
    counts = {"baseline": 0, "candidate": 0}

    def counted(text: str, *, limit: int):
        name = active_arm["name"]
        if name in counts and limit == 24:
            counts[name] += 1
        return original_query_terms(text, limit=limit)

    planner_module._query_terms = counted
    try:
        semantic = verify_semantics()
        counts = {"baseline": 0, "candidate": 0}
        paired = []

        for round_index in range(ROUNDS):
            order = ["baseline", "candidate"] if round_index % 2 == 0 else ["candidate", "baseline"]
            timings: dict[str, float] = {}
            for arm in order:
                planner = make_baseline() if arm == "baseline" else AdaptivePlanner()
                task_rows = rows(planner, TASKS)
                active_arm["name"] = arm
                started = time.perf_counter()
                for goal, belief in task_rows:
                    planner.plan(goal, belief, [], recovery=False)
                    planner.plan(goal, belief, [], recovery=True)
                    planner.plan(goal, belief, [], recovery=True)
                timings[arm] = time.perf_counter() - started
                active_arm["name"] = None
            paired.append(
                {
                    "round": round_index + 1,
                    "baseline_seconds": round(timings["baseline"], 6),
                    "candidate_seconds": round(timings["candidate"], 6),
                    "ratio": round(timings["candidate"] / max(timings["baseline"], 1e-12), 4),
                }
            )

        ratios = [row["ratio"] for row in paired]
        median_ratio = statistics.median(ratios)
        expected_baseline = TASKS * 3 * ROUNDS
        expected_candidate = TASKS * ROUNDS
        failures = []
        if semantic["normalized_output_mismatches"]:
            failures.append("normalized planner output changed")
        if not semantic["fresh_recovery_call_identity"]:
            failures.append("recovery ToolCall identity was reused")
        if counts["baseline"] != expected_baseline:
            failures.append(
                f"baseline limit=24 call count {counts['baseline']} != {expected_baseline}"
            )
        if counts["candidate"] != expected_candidate:
            failures.append(
                f"candidate limit=24 call count {counts['candidate']} != {expected_candidate}"
            )
        if median_ratio > RATIO_LIMIT:
            failures.append(f"median ratio {median_ratio:.4f} > {RATIO_LIMIT:.2f}")

        report = {
            "ok": not failures,
            "tasks_per_arm_per_round": TASKS,
            "rounds": ROUNDS,
            "semantic": semantic,
            "limit24_calls": {
                "baseline": counts["baseline"],
                "candidate": counts["candidate"],
                "expected_baseline": expected_baseline,
                "expected_candidate": expected_candidate,
            },
            "paired": paired,
            "median_ratio": round(median_ratio, 4),
            "ratio_limit": RATIO_LIMIT,
            "failures": failures,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not failures else 1
    finally:
        planner_module._query_terms = original_query_terms


if __name__ == "__main__":
    raise SystemExit(main())
