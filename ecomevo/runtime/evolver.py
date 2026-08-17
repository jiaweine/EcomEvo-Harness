from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Iterable

from ecomevo.models import EvolutionPatch, VerificationResult
from .skills import AdaptiveSkillLibrary, RuntimeSkill


class FailureDrivenEvolver:
    """Distill failure/recovery trajectories without a hand-written business value model.

    Verifier-discovered missing evidence may be merged as a *monotonic* planner hardening:
    it can only add a required check, never remove one. Performance-oriented Prompt / Tool /
    Memory / Delegation changes are merely candidates; EvoHarness-VCO owns their shadow
    evaluation and posterior promotion. There is no business-specific tool map, synthetic
    replay-score table, or fixed performance threshold in this layer.
    """

    FORBIDDEN_WEAKENING = {
        "跳过确认", "无需确认", "绕过确认", "忽略证据", "降低证据", "直接执行", "自动退款",
        "自动下架", "自动冻结", "自动通过", "自动拒绝", "bypass", "skip approval", "ignore evidence",
    }

    def __init__(self, skills: AdaptiveSkillLibrary | None = None):
        self.skills = skills

    @staticmethod
    def _dedupe(values: Iterable[Any], limit: int) -> list[str]:
        output: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in output:
                output.append(text)
            if len(output) >= limit:
                break
        return output

    @classmethod
    def _safe_guidance(cls, guidance: str) -> bool:
        low = str(guidance).lower()
        return not any(term.lower() in low for term in cls.FORBIDDEN_WEAKENING)

    @staticmethod
    def _generic_guidance() -> str:
        return "；".join(
            [
                "围绕当前 verifier 证据缺口选择最小必要的只读核对步骤",
                "优先补充可关联、可独立核验的业务事实与适用规则",
                "同一成功调用没有新增参数或新增证据目标时不要机械重复",
                "资料仍不足时停止并请求补证，不得用模型判断替代可验证证据",
            ]
        )

    def build_patch(
        self,
        verification: VerificationResult,
        domain: str,
        *,
        available_tools: set[str] | None = None,
    ) -> EvolutionPatch | None:
        if verification.passed:
            return None
        missing = self._dedupe(
            verification.missing_evidence or verification.issues or ["verification_failed"],
            12,
        )
        guidance = self._generic_guidance()
        body = {
            "domain": domain,
            "when": "verification_failed",
            "niche": "|".join(sorted(missing))[:900],
            "add_required_checks": missing,
            "preferred_tools": [],
            "trigger_terms": missing,
            "guidance": guidance,
            "behavior": "只把 verifier 已确认的缺口加入未来 planner required-check；性能型策略仍需 shadow 验证",
            "candidate_only": False,
            "validation_method": "monotonic_evidence_gate",
            "safety_invariants": [
                "evidence_gate_monotonic",
                "read_only_exploration",
                "side_effect_authority_external",
                "performance_edits_shadow_before_live",
            ],
        }
        score = max(0.0, min(1.0, float(verification.score)))
        return EvolutionPatch(
            patch_id=f"patch-{uuid.uuid4().hex[:10]}",
            created_at=time.time(),
            reason="; ".join(verification.issues or missing or ["verification failed"]),
            target="planner",
            patch=body,
            replay_cases=0,
            regression_before=score,
            regression_after=score,
            # This acceptance is safety-monotonic, not a claim of performance superiority.
            accepted=True,
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
            left, right = cleaned.find("{"), cleaned.rfind("}")
            if left >= 0 and right > left:
                try:
                    value = json.loads(cleaned[left : right + 1])
                    return value if isinstance(value, dict) else None
                except Exception:
                    return None
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

        # The model can annotate a future performance candidate, but these fields are not
        # promoted by this layer. The only live effect of this accepted planner patch is the
        # monotonic add_required_checks list above.
        schema = {
            "name": "可复用认知候选名",
            "guidance": "task-agnostic、只读、可回滚的策略",
            "preferred_tools": ["只能来自给定只读工具集合"],
            "trigger_terms": ["来自失败轨迹的泛化触发条件"],
        }
        prompt = (
            "你在为一个受 deterministic verifier 约束的 Agent Harness 从失败轨迹生成候选认知策略。"
            "只能改进信息获取、只读工具选择、上下文整理和 specialist 委派；"
            "不得降低证据门槛、修改 Sandbox/Verifier/RBAC、扩大凭证范围或绕过人工确认。"
            "候选性能策略不会由这里自动上线，而会进入 verifier-grounded shadow evaluation。只返回 JSON。\n"
            f"业务域：{domain}\n"
            f"证据缺口：{json.dumps(verification.missing_evidence, ensure_ascii=False)}\n"
            f"当前注册的合法只读工具：{json.dumps(sorted(available_tools), ensure_ascii=False)}\n"
            f"轨迹摘要：{json.dumps(trajectory or {}, ensure_ascii=False, default=str)[:7000]}\n"
            f"结构：{json.dumps(schema, ensure_ascii=False)}"
        )
        try:
            raw = await reasoner.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "提出可审计、可回滚、只读的 Harness cognition candidate；不要输出隐藏推理。",
                    },
                    {"role": "user", "content": prompt},
                ],
                assets=[],
                max_tokens=900,
                temperature=0.0,
            )
            candidate = self._json_payload(raw) or {}
            guidance = str(candidate.get("guidance") or "")[:2200].strip()
            preferred = self._dedupe(
                [value for value in candidate.get("preferred_tools", []) if str(value) in available_tools],
                6,
            )
            triggers = self._dedupe(
                candidate.get("trigger_terms", []) or patch.patch.get("trigger_terms", []),
                12,
            )
            if guidance and self._safe_guidance(guidance):
                patch.patch["candidate_name"] = str(candidate.get("name") or "失败轨迹认知候选")[:120]
                patch.patch["candidate_guidance"] = guidance
                patch.patch["candidate_preferred_tools"] = preferred
                patch.patch["candidate_trigger_terms"] = triggers
        except Exception:
            pass
        return patch

    def ingest(self, patch: EvolutionPatch) -> RuntimeSkill | None:
        """Legacy hook: a planner hardening is not automatically promoted into a live skill."""
        return None

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
        if recovery_events <= 0:
            return None
        preferred = self._dedupe([tool for tool in used_tools if tool in available_tools], 6)
        if not preferred:
            return None
        trigger_terms = self._dedupe(
            re.findall(r"[A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{2,8}", goal),
            10,
        )
        score = max(0.0, min(1.0, float(verifier_score)))
        return EvolutionPatch(
            patch_id=f"patch-{uuid.uuid4().hex[:10]}",
            created_at=time.time(),
            reason="verified recovery trajectory candidate",
            target="memory",
            patch={
                "domain": domain,
                "when": "similar_goal",
                "niche": "success:" + "|".join(trigger_terms),
                "name": "已验证恢复轨迹候选",
                "guidance": "把已验证恢复路径作为未来只读认知候选；是否复用由 posterior 与当前 evidence state 决定。",
                "preferred_tools": preferred,
                "trigger_terms": trigger_terms,
                "candidate_only": True,
                "validation_method": "verifier_grounded_shadow_posterior",
                "safety_invariants": [
                    "read_only_exploration",
                    "evidence_gate_monotonic",
                    "side_effect_authority_external",
                ],
            },
            replay_cases=0,
            regression_before=score,
            regression_after=score,
            accepted=False,
        )
