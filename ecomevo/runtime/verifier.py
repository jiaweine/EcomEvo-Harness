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
        evidence_terms={str(term).lower() for x in ok_tools if x.tool == "evidence.search" for hit in (x.data.get('hits') or []) for term in (hit.get('matched') or [])}

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
            if any(x in requested for x in ('法定代表人','法人','负责人')) and '法定代表人' not in fields and 'merchant_legal_representative' not in remote_tags:
                missing.append("可核验的法定代表人/负责人信息")
            if any(x in requested for x in ('注册地址','经营地址','地址')) and '注册地址' not in fields and 'merchant_address' not in remote_tags:
                missing.append("可核验的主体地址信息")
        elif domain == "aftersales":
            data = by_name.get("order.inspect").data if by_name.get("order.inspect") else {}
            if not data.get("asset_order_ids") and 'order_identity' not in remote_tags:
                missing.append("可关联的订单号")
            if not data.get("asset_signals") and 'dispute_fact' not in remote_tags:
                missing.append("履约或争议事实凭证")
            requested=goal.primary.lower()
            asks_amount=(('退款' in requested or '退多少' in requested or '赔付' in requested or '赔偿' in requested) and ('金额' in requested or '多少' in requested or '多少钱' in requested))
            if asks_amount and not data.get('asset_amounts') and 'refund_amount' not in remote_tags:
                missing.append("可核验的订单/退款金额")
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
                has_attachment_match=any(term.lower() in evidence_terms for term in terms)
                if any(term in requested for term in terms) and label not in flags and not has_attachment_match and 'product_claim' not in remote_tags:
                    missing.append(desc)
            if any(term in requested for term in ('价格','售价','促销价','活动价','多少钱')) and not (data.get('asset_prices') or 'product_price' in remote_tags):
                missing.append("当前商品的可核验价格信息")
            # When the question explicitly points at visual/media content, text from a different
            # attachment cannot stand in for actually reading that media.
            media = by_name.get("media.summarize").data if by_name.get("media.summarize") else {}
            rows=media.get('assets') or []
            if any(x in requested for x in ('主图','图片','截图','海报')):
                image_rows=[x for x in rows if str(x.get('mime','')).startswith('image/')]
                if not image_rows:missing.append("待核对的商品图片")
                elif not any(bool(x.get('interpretable')) for x in image_rows) and 'content_image' not in remote_tags:missing.append("可读取的商品图片内容（请使用支持图片的模型）")
            if '视频' in requested:
                video_rows=[x for x in rows if str(x.get('mime','')).startswith('video/')]
                if not video_rows:missing.append("待核对的商品视频")
                elif not any(bool(x.get('interpretable')) for x in video_rows) and 'content_video' not in remote_tags:missing.append("可读取的商品视频内容（请使用支持视频/关键帧的模型）")
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
            rows=media.get('assets') or [];requested=goal.primary.lower()
            if asset_count <= 0 or int(media.get("count", 0) or 0) <= 0:
                missing.append("待审核内容素材")
            image_asked=any(x in requested for x in ('图片','主图','截图','海报'))
            video_asked='视频' in requested
            audio_asked=any(x in requested for x in ('音频','录音','语音'))
            def typed(prefix):return [x for x in rows if str(x.get('mime','')).startswith(prefix)]
            if image_asked:
                image_rows=typed('image/')
                if not image_rows and 'content_image' not in remote_tags:missing.append("待核对的图片素材")
                elif not any(bool(x.get('interpretable')) for x in image_rows) and not ({'content_image','content_observation'} & remote_tags):missing.append("可读取的图片素材内容（请使用支持图片的模型）")
            if video_asked:
                video_rows=typed('video/')
                if not video_rows and 'content_video' not in remote_tags:missing.append("待核对的视频素材")
                elif not any(bool(x.get('interpretable')) for x in video_rows) and not ({'content_video','content_observation'} & remote_tags):missing.append("可读取的视频素材内容（请使用支持视频/关键帧的模型）")
            if audio_asked:
                audio_rows=typed('audio/')
                if not audio_rows and 'content_audio' not in remote_tags:missing.append("待核对的音频素材")
                elif not any(bool(x.get('interpretable')) for x in audio_rows) and not ({'content_audio','content_observation'} & remote_tags):missing.append("可读取的音频素材内容（请使用支持音频的模型）")
            if not (image_asked or video_asked or audio_asked) and int(media.get("interpretable_count", 0) or 0) <= 0 and 'content_observation' not in remote_tags:
                missing.append("可读取的素材内容（请使用支持当前媒体的模型或补充文本）")
        elif domain == "general" and asset_count <= 0 and evidence_hits <= 0:
            # General tasks are read-only, so user-provided facts can still be discussed; mark confidence lower
            # without inventing an evidence gap that blocks the entire conversation.
            pass

        missing = list(dict.fromkeys(missing))
        evidence_complete = not missing
        total_cost=sum(max(0.0,float(x.cost or 0)) for x in tools)
        constraints_satisfied=total_cost <= float(goal.max_tool_cost)+1e-9
        side_effect_safe = all((not a.side_effect) or a.requires_confirmation for a in (actions or []))
        if not evidence_complete and any(a.side_effect for a in (actions or [])):
            side_effect_safe=False
        issues: list[str] = []
        if not evidence_complete:
            issues.append("当前资料不足以支持高影响业务处置")
        if not constraints_satisfied:
            issues.append(f"工具执行成本 {total_cost:.2f} 超出本任务上限 {goal.max_tool_cost:.2f}")
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
        if not constraints_satisfied:
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
