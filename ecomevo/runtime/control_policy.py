from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from ecomevo.models import BeliefState, GoalState, ToolCall, ToolResult, VerificationResult
from .skills import AdaptiveSkillLibrary, RuntimeSkill
from .tools import _query_terms, call


@dataclass
class AgentDecision:
    objective: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    delegations: list[dict[str, Any]] = field(default_factory=list)
    stop: bool = False
    stop_reason: str = ""
    reflection: str = ""
    rejected: list[str] = field(default_factory=list)
    selection_trace: list[dict[str, Any]] = field(default_factory=list)


class DecisionPolicy:
    """Model-facing policy boundary plus deterministic evidence-gain routing.

    The model may propose legal read-only moves, but proposal order is not authority.
    EvoGain re-ranks candidates by expected evidence gain, source authority, novelty,
    learned-skill support, cost and within-round diversity. This lets the runtime use
    smaller/open-weight controllers without delegating the final tool policy to them.
    """

    PURPOSES = {
        "media.summarize": "整理任务中的附件类型和可读取状态",
        "evidence.search": "在附件证据中做定向检索",
        "policy.lookup": "读取当前业务场景的适用规则",
        "catalog.inspect": "核对商品标识、声明和价格事实",
        "merchant.inspect": "核对商家主体、资质字段和历史风险",
        "order.inspect": "核对订单标识、金额和履约争议事实",
        "risk.scan": "从附件事实中聚合独立风险信号",
    }

    # Human-readable evidence channels. These are routing hints, never evidence.
    TOOL_CHANNELS = {
        "media.summarize": {"内容素材", "图片", "视频", "音频", "文档", "可读取", "业务事实"},
        "evidence.search": {"业务事实", "争议证据", "内容素材", "商品信息", "主体", "订单", "风险信号"},
        "policy.lookup": {"适用规则", "规则", "政策", "约束"},
        "catalog.inspect": {"商品信息", "商品", "sku", "spu", "价格", "声明", "资质", "品牌", "授权"},
        "merchant.inspect": {"主体", "资质", "授权", "经营范围", "法定代表人", "注册地址", "历史风险"},
        "order.inspect": {"订单", "履约", "物流", "争议证据", "退款", "金额", "签收", "售后"},
        "risk.scan": {"风险信号", "业务事实", "独立风险信号", "异常", "关联", "风险"},
    }
    CONTRADICTION_TOOLS = {"evidence.search", "policy.lookup", "risk.scan"}
    MIN_EXPECTED_GAIN = 0.42

    def __init__(self, planner, registry, sandbox, skills: AdaptiveSkillLibrary, *, max_calls: int, max_delegations: int):
        self.planner = planner
        self.registry = registry
        self.sandbox = sandbox
        self.skills = skills
        self.max_calls = max_calls
        self.max_delegations = max_delegations

    @staticmethod
    def json_payload(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else None
        except Exception:
            a, b = cleaned.find("{"), cleaned.rfind("}")
            if a >= 0 and b > a:
                try:
                    value = json.loads(cleaned[a:b + 1])
                    return value if isinstance(value, dict) else None
                except Exception:
                    pass
        return None

    def tool_catalog(self, domain: str) -> list[dict[str, Any]]:
        remote = {str(x.get("key")): x for x in getattr(self.registry, "remote_specs", [])}
        rows = []
        for row in self.registry.describe():
            key = str(row.get("key")); spec = remote.get(key) or {}
            if spec and spec.get("domain") not in {None, domain}:
                continue
            rows.append({
                "tool": key,
                "cost": float(row.get("cost", 1.0) or 1.0),
                "mode": row.get("mode", "read-only"),
                "purpose": str(spec.get("purpose") or self.PURPOSES.get(key) or "读取与任务相关的业务事实"),
                "evidence_tags": list(spec.get("evidence_tags") or []),
            })
        return rows

    @staticmethod
    def digest_tool_result(result: ToolResult) -> dict[str, Any]:
        base = {"tool": result.tool, "ok": result.ok, "cost": round(float(result.cost or 0), 3)}
        if not result.ok:
            base["error"] = str(result.error or "")[:240]
            return base
        data = result.data or {}
        if data.get("remote_tool"):
            base.update({"remote": True, "purpose": str(data.get("_purpose") or "企业业务数据核对")[:160],
                         "evidence_tags": list(data.get("_evidence_tags") or [])[:12]})
            return base
        keep = {"count", "interpretable_count", "semantic_count", "query_terms", "hits", "rules",
                "asset_product_ids", "asset_claim_flags", "asset_prices", "asset_company_codes",
                "asset_materials", "asset_fields", "asset_risk_signals", "asset_order_ids",
                "asset_amounts", "asset_signals", "risk_score"}
        compact = {k: data[k] for k in keep if k in data}
        if "hits" in compact:
            compact["hits"] = [{"asset_id": x.get("asset_id"), "name": x.get("name"),
                                "matched": list(x.get("matched") or [])[:8]}
                               for x in (compact.get("hits") or [])[:6] if isinstance(x, dict)]
        base["data"] = compact
        return base

    def observation(self, goal: GoalState, belief: BeliefState, results: list[ToolResult],
                    verification: VerificationResult | None, skills: list[RuntimeSkill],
                    remaining_budget: float, step: int) -> dict[str, Any]:
        return {
            "goal": goal.primary[:1200], "domain": goal.domain.value,
            "constraints": list(goal.constraints), "required_evidence": list(goal.required_evidence),
            "current_missing_evidence": list((verification.missing_evidence if verification else belief.missing_evidence) or []),
            "remaining_tool_budget": round(max(0.0, remaining_budget), 3), "step": step,
            "tools_so_far": [self.digest_tool_result(x) for x in results[-16:]],
            "evolution_policy": self.skills.policy(goal.domain.value),
            "skills": [{"skill_id": s.skill_id, "name": s.name, "guidance": s.guidance[:700],
                        "preferred_tools": s.preferred_tools, "posterior": round(s.posterior_mean, 3)} for s in skills],
        }

    async def ask_controller(self, reasoner, *, observation: dict[str, Any], catalog: list[dict[str, Any]], phase: str):
        if reasoner is None:
            return None
        schema = {
            "objective": "本轮要缩小的关键不确定性",
            "tool_calls": [{"tool": "必须来自工具目录", "purpose": "为什么此刻需要它",
                            "args": {"keywords": ["仅 evidence.search 可填写"]}, "parallel_group": "同组并行"}],
            "delegations": [{"role": "短角色名", "question": "只基于已核对事实要复核什么", "focus_tools": ["工具名"]}],
            "stop": False, "stop_reason": "继续只读探索也无法新增证据时填写",
            "reflection": "一句话记录策略调整，不输出隐藏推理",
        }
        prompt = (
            "你是电商业务运行时的自主控制器，只决定下一步信息获取与只读复核。\n"
            "硬约束：不得调用改变业务状态的工具；不得把用户陈述、历史回答、技能或模型判断当成独立证据；"
            "不得降低证据门槛；不得批准退款、下架、审核、冻结等动作。优先提出能最大幅减少当前证据缺口的少量候选工具，"
            "无需为候选排序负责，运行时会按可审计的信息增益策略重新选择。避免重复已成功且没有新参数的调用。"
            "只返回 JSON，不输出隐藏思维。\n"
            f"阶段：{phase}\n工具目录：{json.dumps(catalog, ensure_ascii=False)}\n"
            f"当前观察：{json.dumps(observation, ensure_ascii=False, default=str)}\n"
            f"返回结构：{json.dumps(schema, ensure_ascii=False)}"
        )
        try:
            raw = await reasoner.chat(messages=[
                {"role": "system", "content": "你是受安全边界约束的自主任务控制器，只输出可审计动作选择。"},
                {"role": "user", "content": prompt}], assets=[], max_tokens=1300, temperature=0.0)
            return self.json_payload(raw)
        except Exception:
            return None

    @staticmethod
    def call_signature(tool: str, args: dict[str, Any]) -> str:
        body = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(f"{tool}|{body}".encode()).hexdigest()

    @staticmethod
    def _terms(value: Any) -> set[str]:
        text = " ".join(str(x) for x in value) if isinstance(value, (list, tuple, set)) else str(value or "")
        terms = set(_query_terms(text, limit=64))
        terms.update(x.lower() for x in re.findall(r"[A-Za-z0-9_-]{2,}", text))
        terms.update(x for x in re.split(r"[\s/、，。；;：:()（）]+", text.lower()) if len(x) >= 2)
        return {x for x in terms if x}

    def _tool_meta(self, tool: str) -> dict[str, Any]:
        remote = next((x for x in getattr(self.registry, "remote_specs", []) if str(x.get("key")) == tool), None) or {}
        described = next((x for x in self.registry.describe() if str(x.get("key")) == tool), None) or {}
        return {
            "mode": described.get("mode", "read-only"),
            "purpose": str(remote.get("purpose") or self.PURPOSES.get(tool) or "读取业务事实"),
            "evidence_tags": list(remote.get("evidence_tags") or []),
        }

    def _candidate_features(self, *, tool: str, cost: float, goal: GoalState, missing: list[str],
                            previous: list[ToolResult], skills: list[RuntimeSkill]) -> tuple[float, dict[str, Any], set[str]]:
        meta = self._tool_meta(tool)
        channels = set(self.TOOL_CHANNELS.get(tool, set()))
        channels.update(str(x) for x in meta["evidence_tags"])
        channel_terms = self._terms(channels | {meta["purpose"]})
        targets = list(missing) or list(goal.required_evidence)

        coverage_scores = []
        for target in targets:
            target_terms = self._terms(target)
            overlap = target_terms & channel_terms
            if overlap:
                coverage_scores.append(min(1.0, 0.42 + 0.18 * len(overlap)))
            elif tool == "evidence.search":
                coverage_scores.append(0.34)
            else:
                coverage_scores.append(0.0)
        coverage = sum(coverage_scores) / max(1, len(coverage_scores))

        authoritative = 1.0 if meta["mode"] == "mcp-read" and meta["evidence_tags"] else 0.0
        skill_support = max((s.posterior_mean for s in skills if tool in s.preferred_tools), default=0.0)
        prior_success = sum(1 for result in previous if result.ok and result.tool == tool)
        novelty = 1.0 / (1.0 + 0.72 * prior_success)
        contradiction = 1.0 if tool in self.CONTRADICTION_TOOLS and bool(targets) else 0.0
        specificity = min(1.0, len(channel_terms) / 10.0)

        # EvoGain: evidence utility per unit of execution cost. The constants are routing weights,
        # not confidence values and never enter business verification.
        raw = (
            1.70 * coverage
            + 0.58 * authoritative
            + 0.48 * skill_support
            + 0.36 * novelty
            + 0.20 * contradiction
            + 0.10 * specificity
        )
        utility = raw / (0.72 + math.pow(max(0.15, cost), 0.68))
        features = {
            "coverage": round(coverage, 3),
            "authority": round(authoritative, 3),
            "novelty": round(novelty, 3),
            "skill": round(skill_support, 3),
            "cost": round(cost, 3),
        }
        return utility, features, {x.lower() for x in channel_terms}

    def _rank_candidates(self, candidates: list[dict[str, Any]], *, goal: GoalState, missing: list[str],
                         previous: list[ToolResult], skills: list[RuntimeSkill], budget: float,
                         limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        pool = []
        for position, candidate in enumerate(candidates):
            utility, features, channels = self._candidate_features(
                tool=candidate["tool"], cost=candidate["cost"], goal=goal, missing=missing,
                previous=previous, skills=skills,
            )
            pool.append({**candidate, "base_utility": utility, "features": features,
                         "channels": channels, "position": position})

        selected: list[dict[str, Any]] = []
        selected_channels: set[str] = set()
        remaining_budget = max(0.0, budget)
        trace: list[dict[str, Any]] = []

        while pool and len(selected) < max(1, limit):
            scored = []
            for item in pool:
                if item["cost"] > remaining_budget + 1e-9:
                    adjusted = -1.0
                    overlap = 0.0
                else:
                    overlap = (len(item["channels"] & selected_channels) / max(1, len(item["channels"]))) if selected_channels else 0.0
                    # Diversity makes parallel calls cover different evidence channels rather than echo each other.
                    adjusted = item["base_utility"] - 0.24 * overlap - 0.002 * item["position"]
                scored.append((adjusted, -item["cost"], item, overlap))
            scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
            adjusted, _, best, overlap = scored[0]
            pool.remove(best)
            if adjusted < self.MIN_EXPECTED_GAIN:
                trace.append({"tool": best["tool"], "selected": False, "utility": round(max(0.0, adjusted), 3),
                              "reason": "low_expected_gain", **best["features"]})
                continue
            selected.append(best)
            selected_channels.update(best["channels"])
            remaining_budget -= best["cost"]
            trace.append({"tool": best["tool"], "selected": True, "utility": round(adjusted, 3),
                          "diversity_overlap": round(overlap, 3), **best["features"]})

        selected_ids = {id(x) for x in selected}
        for item in pool:
            if len(trace) >= 12:
                break
            trace.append({"tool": item["tool"], "selected": id(item) in selected_ids,
                          "utility": round(max(0.0, item["base_utility"]), 3),
                          "reason": "budget_or_lower_gain", **item["features"]})
        return selected, trace[:12]

    def sanitize(self, raw: dict[str, Any] | None, *, goal: GoalState, remaining_budget: float,
                 previous: list[ToolResult], skills: list[RuntimeSkill], phase: str,
                 missing_evidence: list[str] | None = None) -> AgentDecision:
        raw = raw if isinstance(raw, dict) else {}
        decision = AgentDecision(objective=str(raw.get("objective") or "")[:500], stop=bool(raw.get("stop", False)),
                                 stop_reason=str(raw.get("stop_reason") or "")[:500],
                                 reflection=str(raw.get("reflection") or "")[:700])
        successful = {r.tool for r in previous if r.ok and r.tool != "evidence.search"}
        candidates = [x for x in (raw.get("tool_calls") or []) if isinstance(x, dict)]
        for skill in skills:
            candidates.extend({"tool": t, "purpose": f"技能建议：{skill.name}", "args": {}, "parallel_group": "skill"}
                              for t in skill.preferred_tools)

        meta = self.skills.policy(goal.domain.value)
        exploration = max(0.0, min(1.0, float(meta.get("exploration", .6))))
        call_limit = max(1, min(self.max_calls, round(self.max_calls * (.72 + .48 * exploration))))
        delegation_limit = max(0, min(self.max_delegations, round(self.max_delegations * (.65 + .55 * exploration))))
        budget = max(0.0, float(remaining_budget)); seen: set[str] = set(); legal: list[dict[str, Any]] = []

        for item in candidates:
            tool = str(item.get("tool") or "").strip(); impl = self.registry.tools.get(tool)
            if not impl:
                decision.rejected.append(f"unknown:{tool}"); continue
            gate = self.sandbox.validate_tool(tool)
            if not gate.allowed or gate.requires_confirmation:
                decision.rejected.append(f"unsafe:{tool}"); continue
            if tool in successful:
                decision.rejected.append(f"redundant:{tool}"); continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            if tool == "evidence.search":
                words = [str(x).strip() for x in (args.get("keywords") or []) if str(x).strip()][:32]
                args = {"keywords": words or _query_terms(" ".join([goal.primary] + list(goal.required_evidence)), limit=24)}
            else:
                args = {}  # MCP and local tool arguments remain server-owned.
            sig = self.call_signature(tool, args)
            if sig in seen:
                continue
            seen.add(sig)
            cost = float(getattr(impl, "cost", 1.0) or 1.0)
            legal.append({
                "tool": tool, "args": args, "cost": cost,
                "purpose": str(item.get("purpose") or self.PURPOSES.get(tool) or "自主补充核对")[:300],
                "group": str(item.get("parallel_group") or f"autonomy-{phase}")[:80],
            })

        missing = list(missing_evidence or goal.required_evidence)
        ranked, trace = self._rank_candidates(
            legal, goal=goal, missing=missing, previous=previous, skills=skills,
            budget=budget, limit=call_limit,
        )
        decision.selection_trace = trace
        for item in ranked:
            decision.calls.append(call(item["tool"], item["purpose"], item["args"], cost=item["cost"], group=item["group"]))

        for item in (raw.get("delegations") or []):
            if len(decision.delegations) >= delegation_limit or not isinstance(item, dict):
                break
            role = re.sub(r"[^\w\u4e00-\u9fff -]", "", str(item.get("role") or "专项复核"))[:40].strip() or "专项复核"
            focus = [str(x) for x in (item.get("focus_tools") or []) if str(x) in self.registry.tools][:8]
            decision.delegations.append({"role": role, "question": str(item.get("question") or "复核证据缺口")[:700], "focus_tools": focus})
        return decision

    def fallback_calls(self, goal: GoalState, belief: BeliefState, assets: list[dict[str, Any]], *,
                       remaining_budget: float, previous: list[ToolResult], skills: list[RuntimeSkill]) -> list[ToolCall]:
        candidates = list(self.planner.plan(goal, belief, assets, recovery=True)) + list(self.registry.planned_calls(goal.domain.value, recovery=True))
        raw_calls = []
        for item in candidates:
            args = item.args or {}
            if item.tool == "evidence.search":
                args = {"keywords": list(dict.fromkeys(list(belief.missing_evidence) + _query_terms(goal.primary, limit=16)))[:32]}
            raw_calls.append({"tool": item.tool, "purpose": item.purpose, "args": args,
                              "parallel_group": item.parallel_group})
        decision = self.sanitize(
            {"objective": "根据证据缺口选择最高信息增益的兜底核对", "tool_calls": raw_calls},
            goal=goal, remaining_budget=remaining_budget, previous=previous, skills=skills, phase="fallback",
            missing_evidence=list(belief.missing_evidence),
        )
        return decision.calls
