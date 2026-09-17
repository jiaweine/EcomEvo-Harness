from __future__ import annotations

import pytest

from ecomevo.runtime.neural_rerank import NeuralEvidenceReranker


def _hits():
    return [
        {
            "asset_id": "a-1",
            "name": "订单主记录",
            "rank": 1,
            "channels": ["exact", "contextual_bm25"],
            "snippet": "订单 ORD-88421 已签收。",
            "passages": [{"snippet": "订单 ORD-88421 已签收。"}],
        },
        {
            "asset_id": "a-2",
            "name": "物流回执",
            "rank": 2,
            "channels": ["contextual_bm25", "dense"],
            "snippet": "物流回执说明签收时间和签收人。",
            "passages": [{"snippet": "物流回执说明签收时间和签收人。"}],
        },
        {
            "asset_id": "a-3",
            "name": "商家说明",
            "rank": 3,
            "channels": ["dense"],
            "snippet": "商家补充了售后说明。",
            "passages": [{"snippet": "商家补充了售后说明。"}],
        },
    ]


def test_rank_fusion_keeps_original_retrieval_signal_while_allowing_neural_promotion():
    reranked = NeuralEvidenceReranker._fuse_ranks(_hits(), [0.1, 0.95, 0.2])

    assert reranked[0]["asset_id"] == "a-2"
    assert reranked[0]["rank"] == 1
    assert "neural_rerank" in reranked[0]["channels"]
    assert all("rerank_fusion" in row for row in reranked[:3])


@pytest.mark.asyncio
async def test_reranker_is_offline_by_default(monkeypatch):
    for key in (
        "ECOMEVO_RETRIEVAL_RERANK_URL",
        "ECOMEVO_RETRIEVAL_RERANK_MODEL",
        "ECOMEVO_RETRIEVAL_RERANK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    original = _hits()
    reranked, status = await NeuralEvidenceReranker.rerank("订单是否签收", original)

    assert status == "not_configured"
    assert reranked == original


@pytest.mark.asyncio
async def test_reranker_falls_back_without_mutating_original_order(monkeypatch):
    async def fake_scores(cls, query, hits):
        return None, "fallback"

    monkeypatch.setattr(NeuralEvidenceReranker, "_scores", classmethod(fake_scores))
    original = _hits()
    reranked, status = await NeuralEvidenceReranker.rerank("订单是否签收", original)

    assert status == "fallback"
    assert [row["asset_id"] for row in reranked] == ["a-1", "a-2", "a-3"]


@pytest.mark.asyncio
async def test_reranker_uses_validated_scores_when_available(monkeypatch):
    async def fake_scores(cls, query, hits):
        return [0.05, 0.99, 0.1], "used"

    monkeypatch.setattr(NeuralEvidenceReranker, "_scores", classmethod(fake_scores))
    reranked, status = await NeuralEvidenceReranker.rerank("订单签收依据", _hits())

    assert status == "used"
    assert reranked[0]["asset_id"] == "a-2"
    assert reranked[0]["rerank_score"] == 0.99
