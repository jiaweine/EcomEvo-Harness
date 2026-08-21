from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from ecomevo.models import BeliefState, GoalState, SubAgentResult, ToolResult, VerificationResult
from .control_policy import DecisionPolicy
from .delegation import CognitiveDelegator
from .skills import AdaptiveSkillLibrary

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]
CheckpointFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
RestoreFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass
class TaskNode:
    node_id: str
    kind: str
    label: str
    status: str = "pending"
    parents: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def as_dict(self):
        return {"node_id": self.node_id, "kind": self.kind, "label": self.label, "status": self.status,
                "parents": list(self.parents), "payload": dict(self.payload), "created_at": self.created_at,
                "finished_at": self.finished_at}


class TaskGraph:
    def __init__(self):
        self.nodes: dict[str, TaskNode] = {}; self.order: list[str] = []

    def add(self, kind: str, label: str, *, parents: Iterable[str] = (), payload: dict[str, Any] | None = None):
        node_id = f"node-{uuid.uuid4().hex[:10]}"; node = TaskNode(node_id, kind, str(label)[:180], parents=list(parents), payload=payload or {})
        self.nodes[node_id] = node; self.order.append(node_id); return node_id

    def finish(self, node_id: str, status: str = "completed", payload: dict[str, Any] | None = None):
        node = self.nodes.get(node_id)
        if not node:
            return
        node.status = status; node.finished_at = time.time()
        if payload:
            node.payload.update(payload)

    def snapshot(self, limit: int = 160):
        ids = self.order[-max(1, int(limit)):]
        return {"node_count": len(self.order), "nodes": [self.nodes[x].as_dict() for x in ids if x in self.nodes]}


@dataclass
class AutonomyOutcome:
    tool_results: list[ToolResult]
    agents: list[SubAgentResult]
    verification: VerificationResult
    autonomy_steps: int
    delegations: int
    recovery_events: int
    task_graph: dict[str, Any]
    skills_used: list[str]
    stagnated: bool = False
    stop_reason: str = "evidence_incomplete"
    stop_detail: str = "当前证据仍不足以完成最终验证"


class AutonomousController:
    """Bounded observe-decide-act-review-verify loop with deterministic outer authority."""

    STOP_LABELS = {
        "verified": "证据验证完成",
        "budget_exhausted": "只读工具预算已用尽",
        "controller_stop": "认知控制器建议停止",
        "no_high_value_action": "没有更高价值的下一步",
        "stagnated": "继续补证未改变状态",
        "step_limit": "达到自主处理步数上限",
        "evidence_incomplete": "证据仍不完整",
    }

    def __init__(self, planner, registry, executor, sandbox, verifier, reviewer, skills: AdaptiveSkillLibrary):
        self.planner = planner; self.registry = registry; self.executor = executor; self.sandbox = sandbox
        self.verifier = verifier; self.reviewer = reviewer; self.skills = skills
        self.max_steps = max(2, min(10, int(os.environ.get("ECOMEVO_AUTONOMY_STEPS", "6"))))
        self.max_calls = max(1, min(6, int(os.environ.get("ECOMEVO_AUTONOMY_CALLS_PER_STEP", "4"))))
        self.max_delegations = max(0, min(4, int(os.environ.get("ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP", "3"))))
        self.policy = DecisionPolicy(planner, registry, sandbox, skills, max_calls=self.max_calls, max_delegations=self.max_delegations)
        self.delegator = CognitiveDelegator(reviewer, self.policy)

    @staticmethod
    def _fingerprint(v: VerificationResult, results: list[ToolResult]):
        payload = {"missing": sorted(v.missing_evidence), "ok_tools": sorted({x.tool for x in results if x.ok}),
                   "evidence_tags": sorted({str(t) for x in results if x.ok for t in (x.data.get("_evidence_tags") or [])})}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    async def run(self, *, goal: GoalState, belief: BeliefState, assets: list[dict[str, Any]], text: str,
                  context: dict[str, Any], reasoner=None, emit: EmitFn,
                  checkpoint: CheckpointFn | None = None, restore: RestoreFn | None = None) -> AutonomyOutcome:
        graph = TaskGraph(); root = graph.add("goal", goal.primary[:160], payload={"domain": goal.domain.value})
        total: list[ToolResult] = []; agents: list[SubAgentResult] = []; skills_used: list[str] = []
        delegated_count = recovery_events = autonomy_steps = 0; stagnated = False
        verification: VerificationResult | None = None

        async def make_checkpoint(stage: str) -> dict[str, Any]:
            state = {
                "stage": stage,
                "goal": goal.model_dump(mode="json"),
                "belief": belief.model_dump(mode="json"),
                "tool_result_count": len(total),
                "review_count": len(agents),
            }
            return await checkpoint(stage, state) if checkpoint is not None else {"stage": stage}

        async def rollback_belief(reference: dict[str, Any], reason: list[str]) -> bool:
            restored = await restore(reference) if restore is not None else None
            restored_belief = (restored or {}).get("belief")
            if isinstance(restored_belief, dict):
                clean = BeliefState.model_validate(restored_belief)
                belief.facts = clean.facts
                belief.risks = clean.risks
                belief.uncertainties = clean.uncertainties
                belief.missing_evidence = clean.missing_evidence
                belief.confidence = clean.confidence
                ok = True
            else:
                ok = False
            await emit("runtime.rollback", {
                "restored": ok,
                "reason": reason,
                "mode": "checkpoint_restore_then_replan",
                "checkpoint_seq": reference.get("seq"),
                "checkpoint_state_hash": reference.get("state_hash"),
            })
            return ok

        async def finish(reason: str, detail: str) -> AutonomyOutcome:
            nonlocal verification
            assert verification is not None
            safe_reason = reason if reason in self.STOP_LABELS else "evidence_incomplete"
            safe_detail = str(detail or self.STOP_LABELS[safe_reason])[:300]
            parents = [graph.order[-1]] if graph.order else [root]
            stop_node = graph.add("stop", self.STOP_LABELS[safe_reason], parents=parents,
                                  payload={"reason": safe_reason, "evidence_complete": verification.evidence_complete,
                                           "missing_evidence": list(verification.missing_evidence)})
            graph.finish(stop_node)
            spent = round(sum(max(0.0, float(x.cost or 0.0)) for x in total), 3)
            await emit("autonomy.stopped", {
                "reason": safe_reason,
                "detail": safe_detail,
                "evidence_complete": bool(verification.evidence_complete),
                "missing_evidence": list(verification.missing_evidence),
                "tool_cost_used": spent,
                "tool_cost_budget": round(float(goal.max_tool_cost), 3),
                "tool_cost_remaining": round(max(0.0, float(goal.max_tool_cost) - spent), 3),
                "autonomy_steps": autonomy_steps,
                "stagnated": bool(stagnated),
            })
            return AutonomyOutcome(total, agents, verification, autonomy_steps, delegated_count, recovery_events,
                                   graph.snapshot(), list(dict.fromkeys(skills_used)), stagnated, safe_reason, safe_detail)

        initial_skills = self.skills.relevant(goal.domain.value, query=goal.primary, missing=belief.missing_evidence)
        skills_used.extend(x.skill_id for x in initial_skills)
        recovery_checkpoint = await make_checkpoint("before_initial_plan")
        plan = list(self.planner.plan(goal, belief, assets))
        for remote in self.registry.planned_calls(goal.domain.value):
            if sum(x.estimated_cost for x in plan) + remote.estimated_cost <= goal.max_tool_cost:
                plan.append(remote)
        initial_delegations = []
        if reasoner is not None:
            remaining = max(0.0, goal.max_tool_cost - sum(float(x.estimated_cost) for x in plan))
            raw = await self.policy.ask_controller(reasoner, observation=self.policy.observation(goal, belief, total, None, initial_skills, remaining, 0),
                                                   catalog=self.policy.tool_catalog(goal.domain.value), phase="initial")
            extra = self.policy.sanitize(raw, goal=goal, remaining_budget=remaining, previous=[], skills=initial_skills,
                                         phase="initial", missing_evidence=list(belief.missing_evidence) or list(goal.required_evidence))
            existing = {self.policy.call_signature(c.tool, c.args) for c in plan}
            for item in extra.calls:
                sig = self.policy.call_signature(item.tool, item.args)
                if sig not in existing:
                    plan.append(item); existing.add(sig)
            initial_delegations = extra.delegations; autonomy_steps += 1
            await emit("autonomy.decided", {"step": 0, "phase": "initial", "objective": extra.objective,
                                             "calls": [x.model_dump() for x in extra.calls], "delegations": extra.delegations,
                                             "rejected": extra.rejected, "reflection": extra.reflection,
                                             "evogain": extra.selection_trace})
            if extra.rejected:
                await emit("autonomy.decision_rejected", {"step": 0, "phase": "initial", "rejected": extra.rejected})
        await emit("plan.created", {"calls": [x.model_dump() for x in plan], "estimated_cost": sum(x.estimated_cost for x in plan),
                                    "learned_checks": self.planner.evolution_state().get(goal.domain.value, []),
                                    "skills": [x.as_dict() for x in initial_skills]})
        plan_node = graph.add("plan", "初始证据计划", parents=[root], payload={"calls": [x.tool for x in plan]})
        first = await self.executor.execute(plan, context); total.extend(first); graph.finish(plan_node, payload={"result_count": len(first)})
        await emit("tools.completed", {"results": [x.model_dump() for x in first]})
        agents, delegated = await self.delegator.review(goal, total, reasoner, initial_delegations, belief.missing_evidence, emit, graph, [plan_node])
        delegated_count += delegated; await emit("review.completed", {"reviews": [x.model_dump() for x in agents]})
        verification = self.verifier.verify(goal, belief, total, agents); await emit("verification.checked", verification.model_dump())
        if verification.passed:
            return await finish("verified", "证据和约束已通过验证")

        await rollback_belief(recovery_checkpoint, verification.issues + verification.missing_evidence)
        belief.missing_evidence = list(verification.missing_evidence); previous_fp = self._fingerprint(verification, total); stagnant_rounds = 0
        stop_reason = ""; stop_detail = ""
        for step in range(1, self.max_steps + 1):
            spent = sum(max(0.0, float(x.cost or 0)) for x in total); remaining = max(0.0, float(goal.max_tool_cost) - spent)
            if remaining <= .05:
                stop_reason = "budget_exhausted"; stop_detail = "本轮只读工具预算已用尽"; break
            active = self.skills.relevant(goal.domain.value, query=goal.primary, missing=verification.missing_evidence)
            for skill in active:
                if skill.skill_id not in skills_used:
                    skills_used.append(skill.skill_id)
            raw = await self.policy.ask_controller(reasoner, observation=self.policy.observation(goal, belief, total, verification, active, remaining, step),
                                                   catalog=self.policy.tool_catalog(goal.domain.value), phase="recovery") if reasoner is not None else None
            decision = self.policy.sanitize(raw, goal=goal, remaining_budget=remaining, previous=total, skills=active,
                                            phase="recovery", missing_evidence=list(verification.missing_evidence)); autonomy_steps += 1
            if not decision.calls and not decision.stop:
                decision.calls = self.policy.fallback_calls(goal, belief, assets, remaining_budget=remaining, previous=total, skills=active)
                if decision.calls and not decision.objective:
                    decision.objective = "根据证据缺口执行高信息增益补充核对"
            if reasoner is not None and stagnant_rounds >= 1 and len(decision.delegations) < self.max_delegations:
                decision.delegations.append({"role": "反证审查", "question": "寻找遗漏的证据来源、冲突或错误停止条件，只给下一步只读核对方向。", "focus_tools": []})
                await emit("topology.mutated", {"step": step, "reason": "stagnation", "added_role": "反证审查", "authority": "read-only"})
            await emit("autonomy.decided", {"step": step, "phase": "recovery", "objective": decision.objective,
                                             "calls": [x.model_dump() for x in decision.calls], "delegations": decision.delegations,
                                             "stop": decision.stop, "stop_reason": decision.stop_reason, "rejected": decision.rejected,
                                             "reflection": decision.reflection, "remaining_budget": round(remaining, 3),
                                             "evogain": decision.selection_trace})
            if decision.rejected:
                await emit("autonomy.decision_rejected", {"step": step, "phase": "recovery", "rejected": decision.rejected})
            if decision.stop and not decision.calls and not decision.delegations:
                stop_reason = "controller_stop"; stop_detail = "认知控制器建议停止继续查证；最终状态仍由验证器决定"; break
            if not decision.calls and not decision.delegations:
                stop_reason = "no_high_value_action"; stop_detail = "当前预算和证据状态下没有更高价值的只读核对动作"; break
            controller_requested_stop = bool(decision.stop)
            recovery_events += 1
            recovery_checkpoint = await make_checkpoint(f"before_replan_{step}")
            node = graph.add("replan", decision.objective or "自主补充核对", parents=[plan_node],
                             payload={"step": step, "calls": [x.tool for x in decision.calls]})
            await emit("plan.replanned", {"step": step, "calls": [x.model_dump() for x in decision.calls], "spent_cost": round(spent, 3),
                                          "remaining_budget": round(remaining, 3), "skills": [x.skill_id for x in active]})
            more = await self.executor.execute(decision.calls, context) if decision.calls else []; total.extend(more)
            graph.finish(node, payload={"result_count": len(more)}); await emit("tools.recovery_completed", {"step": step, "results": [x.model_dump() for x in more]})
            agents, delegated = await self.delegator.review(goal, total, reasoner, decision.delegations, verification.missing_evidence, emit, graph, [node])
            delegated_count += delegated; verification = self.verifier.verify(goal, belief, total, agents)
            belief.missing_evidence = list(verification.missing_evidence); await emit("verification.rechecked", {**verification.model_dump(), "step": step})
            if verification.passed:
                stop_reason = "verified"; stop_detail = "证据和约束已通过验证"; break
            if verification.recommendation == "rollback":
                await rollback_belief(recovery_checkpoint, verification.issues + verification.missing_evidence)
                belief.missing_evidence = list(verification.missing_evidence)
                graph.finish(node, status="rolled_back", payload={"checkpoint_seq": recovery_checkpoint.get("seq")})
            if controller_requested_stop:
                stop_reason = "controller_stop"; stop_detail = "认知控制器完成本轮指定核对后建议停止；最终状态仍由验证器决定"; break
            fingerprint = self._fingerprint(verification, total)
            stagnant_rounds = stagnant_rounds + 1 if fingerprint == previous_fp else 0; previous_fp = fingerprint
            if stagnant_rounds >= 2:
                stagnated = True; stop_reason = "stagnated"; stop_detail = "连续补证没有改变可验证状态"
                await emit("autonomy.stagnated", {"step": step, "missing_evidence": verification.missing_evidence}); break
        if not stop_reason:
            stop_reason = "step_limit" if not verification.passed else "verified"
            stop_detail = "已达到本轮自主处理步数上限" if stop_reason == "step_limit" else "证据和约束已通过验证"
        return await finish(stop_reason, stop_detail)
