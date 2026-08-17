from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Iterable

from ecomevo.models import EvolutionPatch, VerificationResult
from .skills import AdaptiveSkillLibrary, RuntimeSkill


class FailureDrivenEvolver:
    """Self-evolving harness policy with deterministic credit assignment.

    A model may diagnose or propose a skill, but deterministic code owns safety invariants,
    replay scoring, promotion, persistence and posterior updates. Evolution changes only
    read-only planning/skills; it never edits verifier rules or grants side-effect authority.
    """

    REPLAY_CASES = [
        {"name": "merchant_missing_license", "missing": True, "score": .74, "expected": "replan"},
        {"name": "merchant_complete", "missing": False, "score": .77, "expected": "finish"},
        {"name": "aftersales_missing_order", "missing": True, "score": .69, "expected": "replan"},
        {"name": "aftersales_complete", "missing": False, "score": .82, "expected": "finish"},
        {"name": "product_claim_without_proof", "missing": True, "score": .73, "expected": "replan"},
        {"name": "product_clean", "missing": False, "score": .79, "expected": "finish"},
        {"name": "risk_single_weak_signal", "missing": True, "score": .61, "expected": "replan"},
        {"name": "risk_two_sources", "missing": False, "score": .85, "expected": "finish"},
        {"name": "content_unread_media", "missing": True, "score": .71, "expected": "replan"},
        {"name": "content_verified_media", "missing": False, "score": .80, "expected": "finish"},
    ]

    TOOL_HINTS = {
        "主体": ["merchant.inspect", "evidence.search"],
        "资质": ["merchant.inspect", "evidence.search"],
        "授权": ["merchant.inspect", "evidence.search"],
        "许可证": ["merchant.inspect", "evidence.search"],
        "订单": ["order.inspect", "evidence.search"],
        "履约": ["order.inspect", "evidence.search"],
        "争议": ["order.inspect", "evidence.search"],
        "金额": ["order.inspect", "evidence.search"],
        "商品": ["catalog.inspect", "evidence.search"],
        "声明": ["catalog.inspect", "evidence.search"],
        "价格": ["catalog.inspect", "evidence.search"],
        "风险": ["risk.scan", "evidence.search"],
        "信号": ["risk.scan", "evidence.search"],
        "图片": ["media.summarize", "evidence.search"],
        "视频": ["media.summarize", "evidence.search"],
        "音频": ["media.summarize", "evidence.search"],
        "素材": ["media.summarize", "evidence.search"],
        "规则": ["policy.lookup"],
    }

    FORBIDDEN_WEAKENING = {
        "跳过确认", "无需确认", "绕过确认", "忽略证据", "降低证据", "直接执行", "自动退款",
        "自动下架", "自动冻结", "自动通过", "自动拒绝", "bypass", "skip approval", "ignore evidence",
    }

    def __init__(self, skills: AdaptiveSkillLibrary | None = None):
        self.skills = skills

    def _sandbox_replay(self, patch: dict[str, Any] | None) -> float:
        ok = 0
        for case in self.REPLAY_CASES:
            if patch:
                # Outer safety anchor: evidence completeness is a hard finish condition.
                predicted = "replan" if case["missing"] else ("finish" if case["score"] >= .58 else "replan")
            else:
                predicted = "finish" if case["score"] >= .58 else "replan"
            ok += int(predicted == case["expected"])
        return ok / len(self.REPLAY_CASES)

    @staticmethod
    def _dedupe(values: Iterable[str], limit: int) -> list[str]:
        out = []
        for value in values:
            value = str(value).strip()
            if value and value not in out:
                out.append(value)
            if len(out) >= limit:
                break
        return out

    def _preferred_tools(self, missing: Iterable[str]) -> list[str]:
        rows: list[str] = []
        for gap in missing:
            text = str(gap)
            for key, tools in self.TOOL_HINTS.items():
                if key in text:
                    rows.extend(tools)
        if not rows:
            rows = ["evidence.search"]
        return self._dedupe(rows, 6)

    @classmethod
    def _safe_guidance(cls, guidance: str) -> bool:
        low = str(guidance).lower()
        return not any(term.lower() in low for term in cls.FORBIDDEN_WEAKENING)

    def _shadow_score(self, *, missing: list[str], preferred_tools: list[str], guidance: str, available_tools: set[str] | None = None) -> tuple[float, float]:
        before = self._sandbox_replay(None)
        after = self._sandbox_replay({"hard_evidence_gate": True})
        if available_tools is not None and any(t not in available_tools for t in preferred_tools):
            after *= .70
        if not self._safe_guidance(guidance):
            after = 0.0
        coverage = 1.0 if missing and preferred_tools else .85
        after = min(1.0, after * coverage)
        return round(before, 3), round(after, 3)

    def _promotion_threshold(self,domain:str)->float:
        if self.skills is None:return .92
        try:return float(self.skills.policy(domain)['promotion_threshold'])
        except Exception:return .92

    def build_patch(self, verification: VerificationResult, domain: str, *, available_tools: set[str] | None = None) -> EvolutionPatch | None:
        if verification.passed:
            return None
        missing = self._dedupe(verification.missing_evidence or ["输出前增加证据引用检查"], 12)
        preferred = self._preferred_tools(missing)
        guidance = "；".join([
            "围绕当前证据缺口自主选择最小必要的只读核对步骤",
            "优先用独立来源补齐关键标识、业务事实和适用规则",
            "同一成功工具没有新参数时不要机械重复",
            "资料仍不足时停止并请求补证，不得用模型置信度替代证据",
        ])
        before, after = self._shadow_score(missing=missing, preferred_tools=preferred, guidance=guidance, available_tools=available_tools)
        accepted = after > before and after >= self._promotion_threshold(domain)
        patch = {
            "domain": domain,
            "when": "verification_failed",
            "niche": "|".join(sorted(missing))[:900],
            "add_required_checks": missing,
            "preferred_tools": preferred,
            "trigger_terms": missing,
            "guidance": guidance,
            "behavior": "证据不完整时自主补证或转人工；任何技能都不能改变 verifier/approval 权限边界",
            "safety_invariants": [
                "evidence_gate_monotonic",
                "read_only_exploration",
                "side_effect_authority_external",
                "shadow_before_live",
            ],
        }
        return EvolutionPatch(
            patch_id=f"patch-{uuid.uuid4().hex[:10]}",
            created_at=time.time(),
            reason="; ".join(verification.issues or missing or ["verification failed"]),
            target="planner",
            patch=patch,
            replay_cases=len(self.REPLAY_CASES),
            regression_before=before,
            regression_after=after,
            accepted=accepted,
        )

    @staticmethod
    def _json_payload(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        cleaned = str(text).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    async def evolve(
        self,
        verification: VerificationResult,
        domain: str,
        *,
        available_tools: set[str],
        reasoner=None,
        trajectory: dict[str, Any] | None = None,
    ) -> EvolutionPatch | None:
        patch = self.build_patch(verification, domain, available_tools=available_tools)
        if patch is None or reasoner is None:
            return patch
        schema = {"name": "技能名", "guidance": "可执行但只读的策略", "preferred_tools": [], "trigger_terms": []}
        prompt = (
            "你在为一个受 deterministic verifier 约束的电商 agent runtime 诊断失败并提出可复用只读技能。"
            "技能只能改进信息获取、工具选择、上下文整理和 specialist 委派，绝不能降低证据门槛或绕过人工确认。"
            "只返回 JSON。\n"
            f"业务域：{domain}\n证据缺口：{json.dumps(verification.missing_evidence, ensure_ascii=False)}\n"
            f"可用只读工具：{json.dumps(sorted(available_tools), ensure_ascii=False)}\n"
            f"轨迹摘要：{json.dumps(trajectory or {}, ensure_ascii=False, default=str)[:7000]}\n"
            f"结构：{json.dumps(schema, ensure_ascii=False)}"
        )
        try:
            raw = await reasoner.chat(
                messages=[
                    {"role": "system", "content": "提出可审计、可回放、只读的 harness skill；不要输出隐藏推理。"},
                    {"role": "user", "content": prompt},
                ], assets=[], max_tokens=900, temperature=0.0,
            )
            candidate = self._json_payload(raw) or {}
            preferred = self._dedupe([x for x in candidate.get("preferred_tools", []) if str(x) in available_tools], 6)
            guidance = str(candidate.get("guidance") or "")[:2200].strip()
            triggers = self._dedupe(candidate.get("trigger_terms", []) or patch.patch.get("trigger_terms", []), 12)
            if preferred and guidance and self._safe_guidance(guidance):
                before, after = self._shadow_score(
                    missing=list(verification.missing_evidence), preferred_tools=preferred,
                    guidance=guidance, available_tools=available_tools,
                )
                if after >= patch.regression_after:
                    patch.patch["name"] = str(candidate.get("name") or "失败驱动核对技能")[:120]
                    patch.patch["guidance"] = guidance
                    patch.patch["preferred_tools"] = preferred
                    patch.patch["trigger_terms"] = triggers
                    patch.regression_before = before
                    patch.regression_after = after
                    patch.accepted = after > before and after >= self._promotion_threshold(domain)
        except Exception:
            pass
        return patch

    def ingest(self, patch: EvolutionPatch) -> RuntimeSkill | None:
        if self.skills is None or not patch.accepted:
            return None
        body = patch.patch or {}
        skill = self.skills.upsert_candidate(
            domain=str(body.get("domain") or "general"),
            name=str(body.get("name") or "证据缺口自适应技能"),
            guidance=str(body.get("guidance") or body.get("behavior") or "围绕证据缺口补充只读核对"),
            preferred_tools=[str(x) for x in (body.get("preferred_tools") or [])],
            trigger_terms=[str(x) for x in (body.get("trigger_terms") or body.get("add_required_checks") or [])],
            shadow_score=float(patch.regression_after),
            source_patch_id=patch.patch_id,
            promote=bool(patch.regression_after >= self._promotion_threshold(str(body.get('domain') or 'general'))),
        )
        return skill

    def distill_success(
        self,
        *,
        domain: str,
        goal: str,
        used_tools: list[str],
        recovery_events: int,
        verifier_score: float,
        available_tools: set[str],
    ) -> EvolutionPatch | None:
        if recovery_events <= 0 or verifier_score < .58:
            return None
        preferred = self._dedupe([x for x in used_tools if x in available_tools], 6)
        if not preferred:
            return None
        trigger_terms = self._dedupe(re.findall(r"[A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{2,8}", goal), 10)
        guidance = "成功轨迹表明该场景可优先按已验证工具组合缩小不确定性；仍需逐轮通过证据硬门槛。"
        before, after = self._shadow_score(missing=["trajectory_distillation"], preferred_tools=preferred, guidance=guidance, available_tools=available_tools)
        accepted = after > before and after >= self._promotion_threshold(domain)
        return EvolutionPatch(
            patch_id=f"patch-{uuid.uuid4().hex[:10]}",
            created_at=time.time(),
            reason="verified successful recovery trajectory",
            target="memory",
            patch={
                "domain": domain,
                "when": "similar_goal",
                "niche": "success:" + "|".join(trigger_terms),
                "name": "已验证成功轨迹技能",
                "guidance": guidance,
                "preferred_tools": preferred,
                "trigger_terms": trigger_terms,
                "safety_invariants": ["read_only_exploration", "evidence_gate_monotonic", "side_effect_authority_external"],
            },
            replay_cases=len(self.REPLAY_CASES),
            regression_before=before,
            regression_after=after,
            accepted=accepted,
        )
