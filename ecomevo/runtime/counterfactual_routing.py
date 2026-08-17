from __future__ import annotations

from typing import Any

from .adaptive_routing import AdaptiveAutonomousController, AdaptiveDecisionPolicy
from .delegation import CognitiveDelegator


class CounterfactualAdaptiveDecisionPolicy(AdaptiveDecisionPolicy):
    """Adaptive EvoGain policy trained from verifier difference rewards.

    Routing features and posterior inference come from AdaptiveDecisionPolicy. This layer
    replaces hand-mixed reward shaping with a deterministic leave-one-out verifier credit:
    how much did this selected tool result change verifiable evidence potential?
    """

    def learn_marginal(
        self,
        *,
        domain: str,
        phase: str,
        trace: list[dict[str, Any]],
        results: list[Any],
        marginal_credit: dict[str, float],
    ) -> dict[str, Any] | None:
        selected = [
            row for row in trace
            if row.get("selected") and isinstance(row.get("feature_vector"), list)
        ]
        if not selected:
            return None
        by_tool = {str(result.tool): result for result in results}
        rewards: list[float] = []
        for item in selected:
            tool = str(item.get("tool") or "")
            result = by_tool.get(tool)
            if result is None:
                continue
            reward = max(-1.25, min(1.50, float(marginal_credit.get(tool, 0.0))))
            vector = [float(value) for value in item["feature_vector"]]
            self.routing.update(
                domain, vector, reward, phase=phase, tool=tool,
                meta={
                    "marginal_credit": reward,
                    "ok": bool(getattr(result, "ok", False)),
                    "credit_method": "verifier_leave_one_out",
                },
            )
            self.routing.update_tool_reliability(
                domain, tool, ok=bool(getattr(result, "ok", False)), reward=reward,
            )
            rewards.append(reward)
        if not rewards:
            return None
        return {
            "domain": domain,
            "phase": phase,
            "updated_calls": len(rewards),
            "mean_credit": round(sum(rewards) / len(rewards), 4),
            "credit_method": "verifier_leave_one_out",
            "policy": self.routing.snapshot(domain),
            "authority": "read-only-routing-only",
        }


class CounterfactualAdaptiveAutonomousController(AdaptiveAutonomousController):
    """Production autonomy controller with bounded counterfactual routing credit."""

    def __init__(self, planner, registry, executor, sandbox, verifier, reviewer, skills):
        super().__init__(planner, registry, executor, sandbox, verifier, reviewer, skills)
        self.policy = CounterfactualAdaptiveDecisionPolicy(
            planner, registry, sandbox, skills,
            max_calls=self.max_calls, max_delegations=self.max_delegations,
        )
        self.delegator = CognitiveDelegator(reviewer, self.policy)

    async def run(self, *, goal, belief, assets, text, context, reasoner=None, emit):
        decisions: dict[int, dict[str, Any]] = {}
        batches: dict[int, list[Any]] = {}
        all_results: list[Any] = []

        def potential(verification) -> float:
            required = max(1, len(list(goal.required_evidence)))
            missing = len(list(verification.missing_evidence or []))
            completeness = max(0.0, min(1.0, 1.0 - missing / required))
            return float(verification.score) + completeness

        def marginal_credits(step: int) -> dict[str, float]:
            batch = batches.get(step, [])
            decision = decisions.get(step) or {}
            selected_tools = {
                str(row.get("tool") or "")
                for row in (decision.get("trace") or [])
                if row.get("selected")
            }
            targets = [result for result in batch if str(result.tool) in selected_tools]
            if not targets:
                return {}

            full = self.verifier.verify(goal, belief, all_results, [])
            full_phi = potential(full)
            out: dict[str, float] = {}
            for target in targets:
                tool = str(target.tool)
                if not bool(getattr(target, "ok", False)):
                    out[tool] = 0.0
                    continue
                without = [result for result in all_results if result is not target]
                counterfactual = self.verifier.verify(goal, belief, without, [])
                marginal = full_phi - potential(counterfactual)
                cost = max(0.0, float(getattr(target, "cost", 0.0) or 0.0))
                out[tool] = max(-1.25, min(1.50, marginal / (1.0 + cost)))
            return out

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
                        ToolResult(**row) for row in (payload.get("results") or [])
                        if isinstance(row, dict)
                    ]
                    batches[0] = batch
                    all_results.extend(batch)
                    return
                if event_type == "tools.recovery_completed":
                    from ecomevo.models import ToolResult
                    step = int(payload.get("step", 0) or 0)
                    batch = [
                        ToolResult(**row) for row in (payload.get("results") or [])
                        if isinstance(row, dict)
                    ]
                    batches[step] = batch
                    all_results.extend(batch)
                    return
                if event_type not in {"verification.checked", "verification.rechecked"}:
                    return
                step = int(payload.get("step", 0) or 0) if event_type == "verification.rechecked" else 0
                decision = decisions.get(step) or {}
                update = self.policy.learn_marginal(
                    domain=goal.domain.value,
                    phase=str(decision.get("phase") or ("initial" if step == 0 else "recovery")),
                    trace=list(decision.get("trace") or []),
                    results=batches.get(step, []),
                    marginal_credit=marginal_credits(step),
                )
                if update:
                    await emit("routing.policy.updated", {**update, "step": step})
            except Exception as exc:
                await emit("routing.policy.learning_error", {
                    "error": type(exc).__name__,
                    "authority": "read-only-routing-only",
                })

        return await super(AdaptiveAutonomousController, self).run(
            goal=goal,
            belief=belief,
            assets=assets,
            text=text,
            context=context,
            reasoner=reasoner,
            emit=learning_emit,
        )
