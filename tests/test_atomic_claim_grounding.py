from __future__ import annotations

from ecomevo.product.grounded_analyzer import AtomicClaimGroundingGuard


def _evidence():
    return [
        {
            "evidence_id": "tool:order-1",
            "source": "order.inspect",
            "kind": "tool_result",
            "title": "订单履约核对",
            "detail": "订单号：ORD-12345；金额：299.0；履约/争议事实：签收、破损",
            "confidence": 0.78,
            "tags": [],
        },
        {
            "evidence_id": "tool:policy-1",
            "source": "policy.lookup",
            "kind": "tool_result",
            "title": "适用规则核对",
            "detail": "争议证据不足时应补证或升级，不应直接定责",
            "confidence": 0.78,
            "tags": [],
        },
        {
            "evidence_id": "tool:mcp-1",
            "source": "mcp.order.live",
            "kind": "tool_result",
            "title": "订单中心实时状态",
            "detail": "订单 ORD-12345 当前状态：已签收",
            "confidence": 0.86,
            "tags": ["mcp", "order_identity"],
        },
    ]


def test_evidence_pack_prioritizes_authoritative_sources():
    packed = AtomicClaimGroundingGuard.evidence_pack(_evidence())
    assert packed[0]["evidence_id"] == "tool:mcp-1"
    assert packed[0]["authority"] > packed[-1]["authority"]


def test_supported_money_and_identifier_survive_typed_anchor_check():
    parsed = {
        "subqueries": [
            {"text": "订单金额是多少", "covered": True, "evidence_ids": ["tool:order-1"]},
        ],
        "claims": [
            {
                "text": "订单 ORD-12345 的金额为 299元",
                "kind": "fact",
                "verdict": "supported",
                "evidence_ids": ["tool:order-1"],
            }
        ],
    }
    result = AtomicClaimGroundingGuard.normalize(parsed, AtomicClaimGroundingGuard.evidence_pack(_evidence()))
    assert result["claims"][0]["verdict"] == "supported"
    assert result["claim_verifiability"] == 1.0
    assert result["query_coverage"] == 1.0


def test_invented_date_is_downgraded_even_when_model_says_supported():
    parsed = {
        "subqueries": [],
        "claims": [
            {
                "text": "订单已于2026-09-10签收",
                "kind": "fact",
                "verdict": "supported",
                "evidence_ids": ["tool:mcp-1"],
            }
        ],
    }
    result = AtomicClaimGroundingGuard.normalize(parsed, AtomicClaimGroundingGuard.evidence_pack(_evidence()))
    claim = result["claims"][0]
    assert claim["verdict"] == "unsupported"
    assert claim["anchor_mismatch"] == ["2026-9-10"]
    assert result["unsupported_factual_claim_count"] == 1


def test_unknown_evidence_id_cannot_unlock_coverage_or_claim_support():
    parsed = {
        "subqueries": [
            {"text": "是否已签收", "covered": True, "evidence_ids": ["tool:invented"]},
        ],
        "claims": [
            {
                "text": "订单 ORD-12345 已签收",
                "kind": "fact",
                "verdict": "supported",
                "evidence_ids": ["tool:invented"],
            }
        ],
    }
    result = AtomicClaimGroundingGuard.normalize(parsed, AtomicClaimGroundingGuard.evidence_pack(_evidence()))
    assert result["query_coverage"] == 0.0
    assert result["claims"][0]["verdict"] == "unsupported"
    assert result["claims"][0]["evidence_ids"] == []


def test_safe_answer_drops_unsupported_fact_and_reports_removal():
    evidence = AtomicClaimGroundingGuard.evidence_pack(_evidence())
    audit = AtomicClaimGroundingGuard.normalize(
        {
            "subqueries": [
                {"text": "订单是否签收", "covered": True, "evidence_ids": ["tool:mcp-1"]},
            ],
            "claims": [
                {
                    "text": "订单 ORD-12345 已签收",
                    "kind": "fact",
                    "verdict": "supported",
                    "evidence_ids": ["tool:mcp-1"],
                },
                {
                    "text": "订单已于2026-09-10签收",
                    "kind": "fact",
                    "verdict": "supported",
                    "evidence_ids": ["tool:mcp-1"],
                },
            ],
        },
        evidence,
    )
    result = {
        "runtime": {"status": "completed", "risks": [], "missing_evidence": []},
        "evidence": _evidence(),
        "actions": [],
    }
    answer = AtomicClaimGroundingGuard.safe_answer(result, audit)
    assert "订单 ORD-12345 已签收" in answer
    assert "2026-09-10" not in answer
    assert "已自动剔除 1 条" in answer


def test_uncovered_subquery_is_exposed_as_next_step():
    audit = AtomicClaimGroundingGuard.normalize(
        {
            "subqueries": [
                {"text": "订单是否签收", "covered": True, "evidence_ids": ["tool:mcp-1"]},
                {"text": "具体签收时间是什么", "covered": False, "evidence_ids": []},
            ],
            "claims": [],
        },
        AtomicClaimGroundingGuard.evidence_pack(_evidence()),
    )
    result = {
        "runtime": {"status": "completed", "risks": [], "missing_evidence": []},
        "evidence": _evidence(),
        "actions": [],
    }
    answer = AtomicClaimGroundingGuard.safe_answer(result, audit)
    assert audit["query_coverage"] == 0.5
    assert "具体签收时间是什么" in answer
