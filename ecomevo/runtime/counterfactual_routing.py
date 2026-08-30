from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from .adaptive_routing import AdaptiveDecisionPolicy
from .autonomy import AutonomousController
from .delegation import CognitiveDelegator
from .factorized_routing import FactorizedAdaptiveRoutingStore


@dataclass(slots=True)
class _DecisionRoundContext:
    """Task-local immutable-input snapshot reused only inside one decision round."""

    domain: str
    skill_policy: dict[str, Any] | None = None
    routing_prepared: dict[str, Any] | None = None
    routing_exploration: float | None = None
    preloaded: bool = False


class _DecisionSkillView:
    """Delegate skill access while memoizing policy metadata for the active task round."""

    def __init__(
        self,
        source: Any,
        decision_round: ContextVar[_DecisionRoundContext | None],
    ) -> None:
        self._source = source
        self._decision_round = decision_round

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    def policy(self, domain: str) -> dict[str, Any]:
        current = self._decision_round.get()
        if current is None or current.domain != str(domain):
            return self._source.policy(domain)
        if current.skill_policy is None:
            current.skill_policy = self._source.policy(domain)
        return current.skill_policy


class _ControllerDecisionSkillView:
    """Fuse built-in decision reads only when this run is about to rank candidates."""

    def __init__(
        self,
        source: Any,
        policy: "CounterfactualAdaptiveDecisionPolicy",
        fuse_next: ContextVar[bool],
    ) -> None:
        self._source = source
        self._policy = policy
        self._fuse_next = fuse_next

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    def relevant(
        self,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        limit: int = 6,
    ) -> list[Any]:
        if self._fuse_next.get():
            prepared = self._policy.prepare_round_skills(
                domain,
                query=query,
                missing=missing,
                limit=limit,
            )
            if prepared is not None:
                return prepared

        # The first no-reasoner call is the initial planner-only round and must stay cheap.
        # Every later ``relevant`` call belongs to recovery and will rank candidates.
        self._fuse_next.set(True)
        return self._source.relevant(
            domain,
            query=query,
            missing=missing,
            limit=limit,
        )


class _DecisionRoutingView:
    """Reuse one posterior/reliability snapshot until verification can update routing."""

    def __init__(
        self,
        source: Any,
        decision_round: ContextVar[_DecisionRoundContext | None],
        tool_keys: Callable[[], list[str]],
    ) -> None:
        self._source = source
        self._decision_round = decision_round
        self._tool_keys = tool_keys

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    def prepare_context(
        self,
        domain: str,
        *,
        tools: list[str],
        exploration: float,
    ) -> dict[str, Any]:
        current = self._decision_round.get()
        if current is None or current.domain != str(domain):
            return self._source.prepare_context(
                domain,
                tools=tools,
                exploration=exploration,
            )

        explore = max(0.0, min(1.0, float(exploration)))
        if (
            current.routing_prepared is not None
            and current.routing_exploration is not None
            and abs(current.routing_exploration - explore) <= 1e-12
        ):
            return current.routing_prepared

        # Legal candidates must exist in registry.tools. Preparing the small complete
        # tool set makes the snapshot reusable when a no-op first decision immediately
        # falls back to planner-generated candidates, without changing any reliability
        # value used by those candidates.
        all_tools = list(
            dict.fromkeys(
                [str(tool) for tool in self._tool_keys() if str(tool)]
                + [str(tool) for tool in tools if str(tool)]
            )
        )
        prepared = self._source.prepare_context(
            domain,
            tools=all_tools,
            exploration=explore,
        )
        current.routing_prepared = prepared
        current.routing_exploration = explore
        return prepared

    def apply_batch(self, *args, **kwargs):
        # Verification credit changes the posterior. Never allow a snapshot prepared
        # before that write to leak into the next decision round.
        self._decision_round.set(None)
        return self._source.apply_batch(*args, **kwargs)


class CounterfactualAdaptiveDecisionPolicy(AdaptiveDecisionPolicy):
    """EvoGain-APR trained from verifier leave-one-out difference credit."""

    def __init__(self, planner, registry, sandbox, skills, *, max_calls: int, max_delegations: int):
        super().__init__(
            planner,
            registry,
            sandbox,
            skills,
            max_calls=max_calls,
            max_delegations=max_delegations,
        )
        # Keep the base/public AdaptiveRoutingStore unchanged. The production
        # Counterfactual policy opts into a scoring-compatible factorized store only.
        self.routing = FactorizedAdaptiveRoutingStore(
            getattr(skills, "path", "outputs/runtime.db")
        )
        self._decision_round: ContextVar[_DecisionRoundContext | None] = ContextVar(
            f"ecomevo-decision-round-{id(self)}",
            default=None,
        )
        self._skill_source = self.skills
        self._routing_source = self.routing
        self.skills = _DecisionSkillView(self._skill_source, self._decision_round)
        self.routing = _DecisionRoutingView(
            self._routing_source,
            self._decision_round,
            lambda: list(self.registry.tools),
        )

    def rebind_skills(self, skills: Any) -> None:
        """Keep the task-local policy view intact when the runtime skill plugin changes."""
        self._decision_round.set(None)
        self._skill_source = skills
        self.skills = _DecisionSkillView(skills, self._decision_round)

    def prepare_round_skills(
        self,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        limit: int = 6,
    ) -> list[Any] | None:
        """Optionally preload built-in skills/policy/routing from one SQLite snapshot."""
        prepare = getattr(self._skill_source, "prepare_decision_snapshot", None)
        if not callable(prepare):
            self._decision_round.set(None)
            return None
        tools = list(dict.fromkeys(str(tool) for tool in self.registry.tools if str(tool)))
        snapshot = prepare(
            self._routing_source,
            str(domain),
            query=query,
            missing=missing,
            tools=tools,
            limit=limit,
        )
        if not isinstance(snapshot, dict):
            self._decision_round.set(None)
            return None
        policy = snapshot.get("policy")
        routing = snapshot.get("routing")
        if not isinstance(policy, dict) or not isinstance(routing, dict):
            self._decision_round.set(None)
            return None
        exploration = max(
            0.0,
            min(1.0, float(snapshot.get("exploration", policy.get("exploration", 0.6)))),
        )
        self._decision_round.set(
            _DecisionRoundContext(
                domain=str(domain),
                skill_policy=dict(policy),
                routing_prepared=routing,
                routing_exploration=exploration,
                preloaded=True,
            )
        )
        return list(snapshot.get("skills") or [])

    def sanitize(
        self,
        raw: dict[str, Any] | None,
        *,
        goal,
        remaining_budget: float,
        previous: list[Any],
        skills: list[Any],
        phase: str,
        missing_evidence: list[str] | None = None,
    ):
        # A non-fallback sanitize normally starts a fresh decision round. A preloaded
        # fused snapshot already represents this exact round, so consume that marker and
        # retain its coherent policy/routing values. The nested fallback may reuse them.
        current = self._decision_round.get()
        domain = str(goal.domain.value)
        if phase != "fallback":
            if current is None or current.domain != domain or not current.preloaded:
                self._decision_round.set(_DecisionRoundContext(domain=domain))
            else:
                current.preloaded = False
        elif current is None or current.domain != domain:
            self._decision_round.set(_DecisionRoundContext(domain=domain))
        return super().sanitize(
            raw,
            goal=goal,
            remaining_budget=remaining_budget,
            previous=previous,
            skills=skills,
            phase=phase,
            missing_evidence=missing_evidence,
        )

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
        self._decision_read_fusion: ContextVar[bool] = ContextVar(
            f"ecomevo-decision-read-fusion-{id(self)}",
            default=False,
        )
        self._controller_skill_source = skills
        self.skills = _ControllerDecisionSkillView(
            skills,
            self.policy,
            self._decision_read_fusion,
        )

    def rebind(
        self,
        *,
        planner=None,
        registry=None,
        executor=None,
        sandbox=None,
        verifier=None,
        reviewer=None,
        skills=None,
    ) -> None:
        source = skills if skills is not None else self._controller_skill_source
        super().rebind(
            planner=planner,
            registry=registry,
            executor=executor,
            sandbox=sandbox,
            verifier=verifier,
            reviewer=reviewer,
            skills=source,
        )
        self._controller_skill_source = source
        self.policy.rebind_skills(source)
        self.skills = _ControllerDecisionSkillView(
            source,
            self.policy,
            self._decision_read_fusion,
        )

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

        token = self._decision_read_fusion.set(reasoner is not None)
        try:
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
        finally:
            self._decision_read_fusion.reset(token)
