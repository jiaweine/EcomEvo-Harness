from __future__ import annotations

import uuid
from typing import Any

from ecomevo.models import BusinessAction, DecisionDomain, EvidenceRecord, VerificationResult


class GovernanceBoundary:
    """Deterministic conversion from verified runtime state into evidence and proposed actions."""

    @staticmethod
    def evidence(assets: list[dict[str, Any]], tool_results) -> list[EvidenceRecord]:
        out = []
        for asset in assets:
            meta = asset.get("meta", {}); detail = []; tags = []; confidence = .9
            if meta.get("width"): detail.append(f"{meta.get('width')}×{meta.get('height')}")
            if meta.get("duration"): detail.append(f"{meta.get('duration')}s")
            if meta.get("pages"): detail.append(f"{meta.get('pages')}页")
            if meta.get("sha256"):
                tags.append(f"sha256:{meta.get('sha256')}"); detail.append(f"内容指纹 {str(meta.get('sha256'))[:12]}…")
            if meta.get("semantic_text"):
                detail.append(f"已读取多媒体内容（{int(meta.get('semantic_observation_count', 1) or 1)} 项可核对事实）")
                tags.append("multimodal_observation"); confidence = max(.55, min(.95, float(meta.get("semantic_confidence") or .72)))
            out.append(EvidenceRecord(evidence_id=f"asset:{asset.get('id')}", source="upload", kind=meta.get("kind", "file"),
                                      title=asset.get("name", "附件"), detail=" · ".join(detail), confidence=confidence,
                                      asset_id=asset.get("id"), tags=tags))
        for result in tool_results:
            if not result.ok:
                continue
            tags = ["mcp"] + [str(x) for x in (result.data.get("_evidence_tags") or [])] if result.data.get("remote_tool") else []
            title, detail = GovernanceBoundary.tool_evidence_copy(result.tool, result.data)
            out.append(EvidenceRecord(evidence_id=f"tool:{result.call_id}", source=result.tool, kind="tool_result",
                                      title=title, detail=detail, confidence=.86 if tags else .78, tags=tags))
        return out

    @staticmethod
    def tool_evidence_copy(tool: str, data: dict[str, Any]) -> tuple[str, str]:
        if data.get("remote_tool"):
            return str(data.get("_purpose") or "企业业务数据核对"), "已从企业业务系统完成数据核对；详细字段仅用于本次判断，不在页面直接展开。"
        if tool == "media.summarize":
            return "资料内容整理", f"已整理 {int(data.get('count', 0) or 0)} 份资料，其中 {int(data.get('interpretable_count', 0) or 0)} 份内容可直接读取。"
        if tool == "evidence.search":
            pieces = []
            for hit in (data.get("hits") or [])[:3]:
                name = str(hit.get("name") or "资料"); matched = "、".join(str(x) for x in (hit.get("matched") or [])[:4])
                pieces.append(f"{name}：{matched}" if matched else name)
            return "附件证据检索", "；".join(pieces) if pieces else "未找到与当前问题直接匹配的附件片段。"
        if tool == "policy.lookup":
            rules = [str(x) for x in (data.get("rules") or [])[:3]]
            return "适用规则核对", "；".join(rules) if rules else "已完成当前业务场景的规则核对。"
        if tool == "catalog.inspect":
            ids = "、".join(str(x) for x in (data.get("asset_product_ids") or [])[:5]); flags = "、".join(str(x) for x in (data.get("asset_claim_flags") or [])[:5]); detail = []
            if ids: detail.append("商品标识：" + ids)
            if flags: detail.append("资料中需要核对的声明：" + flags)
            return "商品信息核对", "；".join(detail) or "已核对当前资料中的商品字段与声明。"
        if tool == "merchant.inspect":
            codes = "、".join(str(x) for x in (data.get("asset_company_codes") or [])[:3]); materials = "、".join(str(x) for x in (data.get("asset_materials") or [])[:5]); risks = "、".join(str(x) for x in (data.get("asset_risk_signals") or [])[:5]); detail = []
            if codes: detail.append("主体标识：" + codes)
            if materials: detail.append("已见材料：" + materials)
            if risks: detail.append("风险项：" + risks)
            return "商家资质核对", "；".join(detail) or "已核对当前资料中的主体与资质信息。"
        if tool == "order.inspect":
            ids = "、".join(str(x) for x in (data.get("asset_order_ids") or [])[:5]); signals = "、".join(str(x) for x in (data.get("asset_signals") or [])[:5]); amounts = "、".join(str(x) for x in (data.get("asset_amounts") or [])[:5]); detail = []
            if ids: detail.append("订单号：" + ids)
            if amounts: detail.append("金额：" + amounts)
            if signals: detail.append("履约/争议事实：" + signals)
            return "订单履约核对", "；".join(detail) or "已核对当前资料中的订单与履约信息。"
        if tool == "risk.scan":
            parts = [f"{k}：{'、'.join(str(x) for x in v)}" for k, v in list((data.get("asset_signals") or {}).items())[:4]]
            return "风险信号核对", "；".join(parts) if parts else "当前资料中未发现可独立确认的强风险信号。"
        return "业务信息核对", "已完成当前环节的数据核对。"

    @staticmethod
    def actions(domain: DecisionDomain, findings: list[str], risks: list[str], verification: VerificationResult) -> list[BusinessAction]:
        def make(kind, title, description, risk="medium"):
            return BusinessAction(action_id=f"act-{uuid.uuid4().hex[:10]}", kind=kind, title=title, description=description,
                                  risk_level=risk, side_effect=True, requires_confirmation=True,
                                  payload={"verifier_score": verification.score})
        if not verification.passed or not verification.evidence_complete:
            return []
        if domain == DecisionDomain.PRODUCT_GOVERNANCE:
            return [make("listing.review", "提交商品处置复核", "已发现需要核对的商品声明/风险项；确认后进入商品治理队列。", "high" if risks else "medium")]
        if domain == DecisionDomain.MERCHANT_REVIEW:
            return [make("merchant.review", "提交商家审核结论", "将当前资质与风险核对结果提交到商家审核队列。", "high" if risks else "medium")]
        if domain == DecisionDomain.AFTERSALES:
            return [make("aftersales.review", "提交售后判责建议", "将订单、履约和争议证据整理后的建议提交售后处理。")]
        if domain == DecisionDomain.RISK_REVIEW:
            return [make("risk.escalate", "提交风险复核", "将风险信号与证据提交到风险处置队列。", "high")]
        return []
