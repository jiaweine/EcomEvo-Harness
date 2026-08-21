from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from .adaptive_routing import AdaptiveDecisionPolicy
from .autonomy import AutonomousController
from .delegation import CognitiveDelegator


class CounterfactualAdaptiveDecisionPolicy(AdaptiveDecisionPolicy):
    """EvoGain-APR trained from verifier leave-one-out difference credit."""

    def learn_marginal(
        self,
        *,
        domain: str,
        phase: str,
        trace: list[dict[str, Any]],
        results: list[Any],
        marginal_credit: list[tuple[Any, float]],
        counterfactual_ms: float,
    ) -> dict[str, Any] | None:
        selected = [
            row for row in trace
            if row.get("selected") and isinstance(row.get("feature_vector"), list)
        ]
        if not selected or not marginal_credit:
            return None

        result_credits: dict[int, deque[float]] = defaultdict(deque)
        for result, credit in marginal_credit:
            result_credits[id(result)].append(float(credit))

        result_buckets: dict[str, deque[Any]] = defaultdict(deque)
        for result in results:
            result_buckets[str(result.tool)].append(result)

        learning_rows: list[dict[str, Any]] = []
        for item in selected:
            tool = str(item.get("tool") or "")
            bucket = result_buckets.get(tool)
            if not bucket:
                continue
            result = bucket.popleft()
            credits = result_credits.get(id(result))
            reward = credits.popleft() if credits else 0.0
            learning_rows.append({
                "tool": tool,
                "vector": [float(value) for value in item["feature_vector"]],
                "reward": reward,
                "ok": bool(getattr(result, "ok", False)),
                "meta": {
                    "marginal_credit": reward,
                    "ok": bool(getattr(result, "ok", False)),
                    "credit_method": "verifier_leave_one_out_harmonic",
                    "policy_advantage": float(item.get("advantage", 0.0) or 0.0),
                },
            })

        persisted = self.routing.apply_batch(domain, phase=phase, rows=learning_rows)
        if not persisted:
            return None
        rewards = [float(row["reward"]) for row in learning_rows]
        return {
            "domain": domain,
            "phase": phase,
            "updated_calls": len(rewards),
            "mean_credit": round(sum(rewards) / len(rewards), 4),
            "credit_method": "verifier_leave_one_out_harmonic",
            "counterfactual_ms": round(float(counterfactual_ms), 3),
            "policy": persisted,
            "authority": "read-only-routing-only",
        }


class CounterfactualAdaptiveAutonomousController(AutonomousController):
    """Production controller: posterior routing + bounded counterfactual credit."""

    def __init__(self, planner, registry, executor, sandbox, verifier, reviewer, skills):
        super().__init__(planner, registry, executor, sandbox, verifier, reviewer, skills)
        self.policy = CounterfactualAdaptiveDecisionPolicy(
            planner,
            registry,
            sandbox,
            skills,
            max_calls=self.max_calls,
            max_delegations=self.max_delegations,
        )
        self.delegator = CognitiveDelegator(reviewer, self.policy)

    async def run(self, *, goal, belief, assets, text, context, reasoner=None, emit, checkpoint=None, restore=None):
        decisions: dict[int, dict[str, Any]] = {}
        batches: dict[int, list[Any]] = {}
        all_results: list[Any] = []

        def potential(verification) -> float:
            required = max(1, len(list(goal.required_evidence)))
            missing = len(list(verification.missing_evidence or []))
            completeness = max(0.0, min(1.0, 1.0 - missing / required))
            score = max(0.0, min(1.0, float(verification.score)))
            # A high verifier score cannot compensate for poor evidence completeness.
            denom = score + completeness
            return 0.0 if denom <= 1e-12 else (2.0 * score * completeness) / denom

        def marginal_credits(step: int) -> tuple[list[tuple[Any, float]], float]:
            started = time.perf_counter()
            batch = batches.get(step, [])
            decision = decisions.get(step) or {}
            selected_counts: dict[str, int] = defaultdict(int)
            for row in decision.get("trace") or []:
                if row.get("selected"):
                    selected_counts[str(row.get("tool") or "")] += 1

            targets = []
            seen: dict[str, int] = defaultdict(int)
            for result in batch:
                tool = str(result.tool)
                if seen[tool] < selected_counts.get(tool, 0):
                    targets.append(result)
                    seen[tool] += 1
            if not targets:
                return [], (time.perf_counter() - started) * 1000.0

            full = self.verifier.verify(goal, belief, all_results, [])
            full_phi = potential(full)
            credits: list[tuple[Any, float]] = []
            for target in targets:
                if not bool(getattr(target, "ok", False)):
                    credits.append((target, 0.0))
                    continue
                without = [result for result in all_results if result is not target]
                counterfactual = self.verifier.verify(goal, belief, without, [])
                marginal = full_phi - potential(counterfactual)
                cost = max(0.0, float(getattr(target, "cost", 0.0) or 0.0))
                # Phi is bounded to [0,1], so the difference reward is naturally bounded.
                credits.append((target, marginal / (1.0 + cost)))
            return credits, (time.perf_counter() - started) * 1000.0

        async def learning_emit(event_type: str, payload: dict[str, Any]):
            await emit(event_type, payload)
            try:
                if event_type == "autonomy.decided":
                    step = int(payload.get("step", 0) or 0)
                    decisions[step] = {
                        "phase": str(payload.get("phase") or ("initial" if step == 0 else "recovery")),
                        "trace": list(payload.get("evogain") or []),
                    }
                    return
                if event_type == "tools.completed":
                    from ecomevo.models import ToolResult
                    batch = [
                        ToolResult(**row)
                        for row in (payload.get("results") or [])
                        if isinstance(row, dict)
                    ]
                    batches[0] = batch
                    all_results.extend(batch)
                    return
                if event_type == "tools.recovery_completed":
                    from ecomevo.models import ToolResult
                    step = int(payload.get("step", 0) or 0)
                    batch = [
                        ToolResult(**row)
                        for row in (payload.get("results") or [])
                        if isinstance(row, dict)
                    ]
                    batches[step] = batch
                    all_results.extend(batch)
                    return
                if event_type not in {"verification.checked", "verification.rechecked"}:
                    return

                step = int(payload.get("step", 0) or 0) if event_type == "verification.rechecked" else 0
                decision = decisions.get(step) or {}
                credits, counterfactual_ms = marginal_credits(step)
                update = self.policy.learn_marginal(
                    domain=goal.domain.value,
                    phase=str(decision.get("phase") or ("initial" if step == 0 else "recovery")),
                    trace=list(decision.get("trace") or []),
                    results=batches.get(step, []),
                    marginal_credit=credits,
                    counterfactual_ms=counterfactual_ms,
                )
                if update:
                    await emit("routing.policy.updated", {**update, "step": step})
            except Exception as exc:
                await emit(
                    "routing.policy.learning_error",
                    {"error": type(exc).__name__, "authority": "read-only-routing-only"},
                )

        return await super().run(
            goal=goal,
            belief=belief,
            assets=assets,
            text=text,
            context=context,
            reasoner=reasoner,
            emit=learning_emit,
            checkpoint=checkpoint,
            restore=restore,
        )
