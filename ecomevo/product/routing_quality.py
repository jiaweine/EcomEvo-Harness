from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from typing import Any


WINDOW_SECONDS = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


class RoutingQualityControlTower:
    """Tenant-scoped, read-only routing quality observability.

    Every metric is derived from durable conversation messages or task events.
    The service has no write path into routing, policy, skills, actions, or tools.
    """

    MAX_ROWS = 10000

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _json(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _ratio(numerator: int | float, denominator: int | float) -> float | None:
        if denominator <= 0:
            return None
        return round(float(numerator) / float(denominator), 4)

    @classmethod
    def _stats(cls, values: list[float]) -> dict[str, Any]:
        clean = sorted(value for value in values if math.isfinite(value))
        if not clean:
            return {"samples": 0, "avg": None, "p50": None, "p95": None, "min": None, "max": None}

        def percentile(q: float) -> float:
            index = max(0, min(len(clean) - 1, math.ceil(q * len(clean)) - 1))
            return clean[index]

        return {
            "samples": len(clean),
            "avg": round(sum(clean) / len(clean), 4),
            "p50": round(percentile(0.50), 4),
            "p95": round(percentile(0.95), 4),
            "min": round(clean[0], 4),
            "max": round(clean[-1], 4),
        }

    def _rows(self, tenant_id: str, since: float) -> dict[str, Any]:
        with self.store._conn() as db:
            assistants = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT m.id,m.conversation_id,m.payload,m.created_at,c.scene
                    FROM messages m
                    JOIN conversations c ON c.id=m.conversation_id
                    WHERE c.tenant_id=? AND m.role='assistant' AND m.created_at>=?
                    ORDER BY m.created_at ASC,m.id ASC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS + 1),
                ).fetchall()
            ]
            events = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT e.id,e.conversation_id,e.type,e.payload,e.created_at,c.scene
                    FROM task_events e
                    JOIN conversations c ON c.id=e.conversation_id
                    WHERE c.tenant_id=? AND e.created_at>=?
                      AND e.type IN ('autonomy.decided','tools.completed','autonomy.stagnated')
                    ORDER BY e.created_at ASC,e.id ASC
                    LIMIT ?
                    """,
                    (tenant_id, since, self.MAX_ROWS + 1),
                ).fetchall()
            ]

        assistant_truncated = len(assistants) > self.MAX_ROWS
        event_truncated = len(events) > self.MAX_ROWS
        assistants = assistants[: self.MAX_ROWS]
        events = events[: self.MAX_ROWS]
        for row in assistants:
            row["payload"] = self._json(row.get("payload"))
        for row in events:
            row["payload"] = self._json(row.get("payload"))
        return {
            "assistants": assistants,
            "events": events,
            "truncated": assistant_truncated or event_truncated,
        }

    @staticmethod
    def _runtime(payload: dict[str, Any]) -> dict[str, Any]:
        runtime = payload.get("runtime")
        return runtime if isinstance(runtime, dict) else {}

    @classmethod
    def _routing_policy(cls, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = cls._runtime(payload)
        belief = runtime.get("belief")
        belief = belief if isinstance(belief, dict) else {}
        facts = belief.get("facts")
        facts = facts if isinstance(facts, dict) else {}
        policy = facts.get("routing_policy")
        return policy if isinstance(policy, dict) else {}

    def snapshot(
        self,
        *,
        tenant_id: str,
        window: str = "7d",
        now: float | None = None,
    ) -> dict[str, Any]:
        if window not in WINDOW_SECONDS:
            raise ValueError("invalid routing quality window")
        now_ts = float(now if now is not None else time.time())
        since = now_ts - WINDOW_SECONDS[window]
        rows = self._rows(tenant_id, since)
        assistants = rows["assistants"]
        events = rows["events"]

        domain_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        completed_runs = 0
        stagnated_runs = 0
        tool_cost_total = 0.0
        residual_values: list[float] = []
        reward_values: list[float] = []
        policy_sample_values: list[float] = []

        for row in assistants:
            runtime = self._runtime(row["payload"])
            domain = str(runtime.get("domain") or row.get("scene") or "general")
            status = str(runtime.get("status") or "")
            completed_runs += int(status == "completed")
            stagnated_runs += int(bool(runtime.get("stagnated")))
            tool_cost = self._number(runtime.get("tool_cost_used"))
            if tool_cost is not None and tool_cost >= 0:
                tool_cost_total += tool_cost
            policy = self._routing_policy(row["payload"])
            samples = self._number(policy.get("samples"))
            reward = self._number(policy.get("reward_ewma"))
            residual = self._number(policy.get("residual_ewma"))
            observation = {
                "created_at": float(row.get("created_at") or 0.0),
                "samples": int(samples) if samples is not None and samples >= 0 else None,
                "reward_ewma": reward,
                "residual_ewma": residual,
            }
            domain_runs[domain].append(observation)
            if samples is not None and samples >= 0:
                policy_sample_values.append(samples)
            if reward is not None:
                reward_values.append(reward)
            if residual is not None and residual >= 0:
                residual_values.append(residual)

        policy_domains = []
        for domain, observations in sorted(domain_runs.items()):
            usable = [
                row
                for row in observations
                if row["samples"] is not None
                or row["reward_ewma"] is not None
                or row["residual_ewma"] is not None
            ]
            if not usable:
                continue
            first = usable[0]
            latest = usable[-1]
            first_residual = first.get("residual_ewma")
            latest_residual = latest.get("residual_ewma")
            first_reward = first.get("reward_ewma")
            latest_reward = latest.get("reward_ewma")
            policy_domains.append(
                {
                    "domain": domain,
                    "observed_runs": len(observations),
                    "latest_samples": latest.get("samples"),
                    "latest_reward_ewma": latest_reward,
                    "latest_residual_ewma": latest_residual,
                    "residual_delta": (
                        round(float(latest_residual) - float(first_residual), 4)
                        if latest_residual is not None and first_residual is not None
                        else None
                    ),
                    "reward_delta": (
                        round(float(latest_reward) - float(first_reward), 4)
                        if latest_reward is not None and first_reward is not None
                        else None
                    ),
                }
            )

        decision_events = 0
        candidate_count = 0
        selected_count = 0
        nonpositive_abstain = 0
        activations: list[float] = []
        reliabilities: list[float] = []
        diversity_overlaps: list[float] = []
        advantages: list[float] = []

        tool_calls = 0
        successful_calls = 0
        failed_calls = 0
        tool_names: Counter[str] = Counter()
        evidence_tag_yield = 0
        stagnation_events = 0

        for event in events:
            payload = event["payload"]
            event_type = str(event.get("type") or "")
            if event_type == "autonomy.decided":
                decision_events += 1
                trace = payload.get("evogain")
                trace = trace if isinstance(trace, list) else []
                for item in trace:
                    if not isinstance(item, dict):
                        continue
                    candidate_count += 1
                    selected_count += int(bool(item.get("selected")))
                    reason = str(item.get("reason") or "")
                    nonpositive_abstain += int(reason == "abstain_nonpositive_advantage")
                    activation = self._number(item.get("policy_activation"))
                    reliability = self._number(item.get("reliability"))
                    overlap = self._number(item.get("diversity_overlap"))
                    advantage = self._number(item.get("advantage"))
                    if activation is not None:
                        activations.append(activation)
                    if reliability is not None:
                        reliabilities.append(reliability)
                    if overlap is not None:
                        diversity_overlaps.append(overlap)
                    if advantage is not None:
                        advantages.append(advantage)
            elif event_type == "tools.completed":
                results = payload.get("results")
                results = results if isinstance(results, list) else []
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    tool_calls += 1
                    ok = bool(result.get("ok"))
                    successful_calls += int(ok)
                    failed_calls += int(not ok)
                    tool = str(result.get("tool") or "unknown")
                    tool_names[tool] += 1
                    data = result.get("data")
                    data = data if isinstance(data, dict) else {}
                    tags = data.get("_evidence_tags")
                    if isinstance(tags, list):
                        evidence_tag_yield += len({str(tag) for tag in tags if str(tag)})
            elif event_type == "autonomy.stagnated":
                stagnation_events += 1

        return {
            "generated_at": now_ts,
            "tenant_scope": tenant_id,
            "window": {
                "key": window,
                "since": since,
                "until": now_ts,
                "seconds": WINDOW_SECONDS[window],
            },
            "coverage": {
                "assistant_results": len(assistants),
                "task_events": len(events),
                "truncated": bool(rows["truncated"]),
                "max_rows_per_source": self.MAX_ROWS,
            },
            "routing_policy": {
                "domains": policy_domains,
                "samples": self._stats(policy_sample_values),
                "reward_ewma": self._stats(reward_values),
                "residual_ewma": self._stats(residual_values),
            },
            "adaptive_routing": {
                "decision_events": decision_events,
                "candidates_observed": candidate_count,
                "selected_candidates": selected_count,
                "selection_rate": self._ratio(selected_count, candidate_count),
                "abstain_nonpositive_advantage": nonpositive_abstain,
                "activation": self._stats(activations),
                "tool_reliability": self._stats(reliabilities),
                "diversity_overlap": self._stats(diversity_overlaps),
                "advantage": self._stats(advantages),
            },
            "tool_quality": {
                "calls": tool_calls,
                "successful_calls": successful_calls,
                "failed_calls": failed_calls,
                "failed_call_rate": self._ratio(failed_calls, tool_calls),
                "unique_tools": len(tool_names),
                "tool_distribution": dict(sorted(tool_names.items())),
                "evidence_tags_yielded": evidence_tag_yield,
                "evidence_tag_yield_per_call": (
                    round(evidence_tag_yield / tool_calls, 4) if tool_calls else None
                ),
                "evidence_tag_yield_per_successful_call": (
                    round(evidence_tag_yield / successful_calls, 4)
                    if successful_calls
                    else None
                ),
            },
            "stagnation": {
                "stagnated_runs": stagnated_runs,
                "stagnated_run_rate": self._ratio(stagnated_runs, len(assistants)),
                "stagnation_events": stagnation_events,
            },
            "cost_efficiency": {
                "completed_runs": completed_runs,
                "tool_cost_total": round(tool_cost_total, 4),
                "tool_cost_per_completed_run": (
                    round(tool_cost_total / completed_runs, 4) if completed_runs else None
                ),
                "definition": "runtime-reported read-tool cost only; provider cost is reported separately in Observability",
            },
            "authority": {
                "read_only": True,
                "changes_routing": False,
                "changes_policy": False,
                "changes_runtime_skills": False,
                "approves_business_actions": False,
                "executes_tools": False,
            },
            "methodology": {
                "routing_policy": "uses per-result durable belief.facts.routing_policy snapshots; deltas are descriptive, not drift verdicts",
                "adaptive_activation": "uses durable autonomy.decided EvoGain selection traces",
                "tool_reliability": "uses the reliability value recorded on each routed candidate",
                "failed_call_rate": "uses durable tools.completed ToolResult.ok values",
                "evidence_tag_yield": "counts distinct _evidence_tags within each durable ToolResult; it is not a causal evidence-value estimate",
                "read_only": True,
            },
        }
