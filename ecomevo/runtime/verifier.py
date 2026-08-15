from __future__ import annotations
from ecomevo.models import BeliefState, BusinessAction, GoalState, SubAgentResult, ToolResult, VerificationResult


class DecisionVerifier:
    """Verify actual evidence, not merely whether a tool returned successfully."""

    def verify(self, goal: GoalState, belief: BeliefState, tools: list[ToolResult], agents: list[SubAgentResult],
               actions: list[BusinessAction] | None = None) -> VerificationResult:
        ok_tools = [x for x in tools if x.ok]
        by_name: dict[str, ToolResult] = {}
        for result in ok_tools:
            by_name[result.tool] = result
        names = set(by_name)
        remote_tags={str(tag) for result in ok_tools for tag in (result.data.get('_evidence_tags') or [])}
        missing: list[str] = []

        if "policy.lookup" not in names:
            missing.append("适用规则")

        domain = goal.domain.value
        required_tool = {
            "product_governance": ("catalog.inspect", "商品业务信息"),
            "merchant_review": ("merchant.inspect", "主体/资质信息"),
            "aftersales": ("order.inspect", "订单/履约信息"),
            "risk_review": ("risk.scan", "风险信号"),
            "content_audit": ("media.summarize", "内容素材"),
        }.get(domain)
        if required_tool and required_tool[0] not in names:
            missing.append(required_tool[1])

        asset_count = int(belief.facts.get("asset_count", 0) or 0)
        evidence_hits = sum(len(x.data.get("hits", [])) for x in ok_tools if x.tool == "evidence.search")

        # Domain evidence checks intentionally use attachment-derived fields. User wording alone is not treated
        # as independent evidence for actions that can change business state.
        if domain == "merchant_review":
            data = by_name.get("merchant.inspect").data if by_name.get("merchant.inspect") else {}
            # A filename or the literal words "营业执照" are not enough to identify
            # a merchant. Require an attachment-derived company identifier before
            # allowing a review action to leave the workspace.
            if not data.get("asset_company_codes") and 'merchant_identity' not in remote_tags:
                missing.append("可核验的主体标识（如统一社会信用代码）")
            requested=goal.primary.lower();materials=set(data.get('asset_materials') or []);fields=set(data.get('asset_fields') or [])
            if '授权' in requested and not any('授权' in x for x in materials) and 'merchant_authorization' not in remote_tags:
                missing.append("品牌/经营授权材料")
            if '许可证' in requested and '许可证' not in materials and 'merchant_license' not in remote_tags:
                missing.append("对应业务许可证")
            if '经营范围' in requested and '经营范围' not in fields and 'merchant_scope' not in remote_tags:
                missing.append("可核验的经营范围信息")
        elif domain == "aftersales":
            data = by_name.get("order.inspect").data if by_name.get("order.inspect") else {}
            if not data.get("asset_order_ids") and 'order_identity' not in remote_tags:
                missing.append("可关联的订单号")
            if not data.get("asset_signals") and 'dispute_fact' not in remote_tags:
                missing.append("履约或争议事实凭证")
        elif domain == "product_governance":
            data = by_name.get("catalog.inspect").data if by_name.get("catalog.inspect") else {}
            relevant = bool(data.get("asset_product_ids") or data.get("asset_claim_flags") or evidence_hits > 0 or {'product_identity','product_claim'} & remote_tags)
            if (asset_count <= 0 and not ({'product_identity','product_claim'} & remote_tags)) or (asset_count > 0 and int(data.get("asset_text_chars", 0) or 0) < 12 and not ({'product_identity','product_claim'} & remote_tags)) or not relevant:
                missing.append("可关联到当前商品/声明的资料")
            requested=goal.primary.lower();flags=set(data.get('asset_claim_flags') or [])
            claim_requirements=[
                (('治愈','功效'),'功效高风险词','当前商品的功效/治愈声明'),
                (('授权',),'授权链路','当前商品的授权声明或授权材料'),
                (('正品','真伪'),'品牌/真伪声明','当前商品的品牌/真伪声明'),
                (('原装',),'来源声明','当前商品的来源声明'),
                (('100%',),'绝对化表达','当前商品的绝对化表达'),
                (('最低价',),'价格承诺','当前商品的价格承诺'),
            ]
            for terms,label,desc in claim_requirements:
                if any(term in requested for term in terms) and label not in flags and 'product_claim' not in remote_tags:
                    missing.append(desc)
        elif domain == "risk_review":
            data = by_name.get("risk.scan").data if by_name.get("risk.scan") else {}
            asset_signals = data.get("asset_signals", {}) or {}
            flat={str(x) for values in asset_signals.values() for x in values}
            signal_count = len(flat)
            strong=bool(flat & {'套现','伪造','假货','侵权','违禁','虚假物流'}) or 'risk_strong' in remote_tags
            # A generic evidence-search hit (e.g. the word "风险" in a normal report) is not
            # an independent risk signal and must never unlock escalation.
            if signal_count < 2 and not strong:
                missing.append("至少两项独立风险信号或一项强证据")
        elif domain == "content_audit":
            media = by_name.get("media.summarize").data if by_name.get("media.summarize") else {}
            if asset_count <= 0 or int(media.get("count", 0) or 0) <= 0:
                missing.append("待审核内容素材")
            elif int(media.get("interpretable_count", 0) or 0) <= 0 and 'content_observation' not in remote_tags:
                missing.append("可读取的素材内容（请使用支持当前媒体的模型或补充文本）")
        elif domain == "general" and asset_count <= 0 and evidence_hits <= 0:
            # General tasks are read-only, so user-provided facts can still be discussed; mark confidence lower
            # without inventing an evidence gap that blocks the entire conversation.
            pass

        missing = list(dict.fromkeys(missing))
        evidence_complete = not missing
        constraints_satisfied = True
        side_effect_safe = all((not a.side_effect) or a.requires_confirmation for a in (actions or []))
        issues: list[str] = []
        if not evidence_complete:
            issues.append("当前资料不足以支持高影响业务处置")
        if not side_effect_safe:
            issues.append("存在未受控的高影响操作")

        avg_agent = sum(x.confidence for x in agents) / max(1, len(agents))
        # Recovery can call the same tool again. Counting duplicate successful calls
        # as independent certainty made incomplete tasks reach a misleading 1.0.
        score = max(0.0, min(1.0, 0.24 + 0.09 * len(names) + 0.22 * avg_agent + (0.12 if evidence_complete else -0.20)))
        if not evidence_complete:
            score = min(score, 0.49)
        if not side_effect_safe:
            score = min(score, 0.35)
        passed = constraints_satisfied and side_effect_safe and evidence_complete and score >= 0.58
        recommendation = "finish" if passed else ("replan" if score >= 0.35 else "rollback")
        return VerificationResult(
            passed=passed,
            evidence_complete=evidence_complete,
            constraints_satisfied=constraints_satisfied,
            side_effect_safe=side_effect_safe,
            issues=issues,
            missing_evidence=missing,
            recommendation=recommendation,
            score=round(score, 3),
        )
