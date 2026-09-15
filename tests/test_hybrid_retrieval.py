from __future__ import annotations

import pytest

from ecomevo.runtime.hybrid_retrieval import ContextualHybridRetriever


def _asset(asset_id: str, name: str, text: str, **meta):
    return {
        "id": asset_id,
        "name": name,
        "mime": "text/plain",
        "meta": {"kind": "text", "text": text, **meta},
    }


@pytest.mark.asyncio
async def test_exact_identifier_and_contextual_bm25_retrieve_correct_asset(monkeypatch):
    for key in (
        "ECOMEVO_RETRIEVAL_EMBEDDING_BASE_URL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_MODEL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    ctx = {
        "text": "核对订单 ORD-88421 是否已经签收",
        "assets": [
            _asset("a-1", "订单记录.txt", "订单号 ORD-88421，物流状态：已签收，签收备注：本人签收。"),
            _asset("a-2", "商家资料.txt", "商家主体正常，品牌授权材料已提交。"),
        ],
    }
    result = await ContextualHybridRetriever.search(ctx, {})

    assert result["hits"]
    assert result["hits"][0]["asset_id"] == "a-1"
    assert "exact" in result["hits"][0]["channels"]
    assert "contextual_bm25" in result["channels"]
    assert result["dense_status"] == "not_configured"
    assert result["strategy"] == "contextual_hybrid_rrf_mmr_v1"


@pytest.mark.asyncio
async def test_counter_evidence_candidate_is_preserved(monkeypatch):
    for key in (
        "ECOMEVO_RETRIEVAL_EMBEDDING_BASE_URL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_MODEL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    ctx = {
        "text": "这个商家是否有处罚记录",
        "assets": [
            _asset("a-1", "风险核查.txt", "监管核查结果：未发现处罚记录，当前登记状态正常。"),
            _asset("a-2", "无关订单.txt", "订单已完成，没有售后争议。"),
        ],
    }
    result = await ContextualHybridRetriever.search(ctx, {})

    assert result["hits"][0]["asset_id"] == "a-1"
    assert result["hits"][0]["counter_candidate"] is True


@pytest.mark.asyncio
async def test_dense_channel_is_fused_when_embedding_scores_are_available(monkeypatch):
    async def fake_dense(cls, query, chunks):
        # Dense semantics prefers the second document even when the lexical query is vague.
        return [0.1, 0.98], "used"

    monkeypatch.setattr(ContextualHybridRetriever, "_dense_scores", classmethod(fake_dense))
    ctx = {
        "text": "核对这份材料里的主体关系",
        "assets": [
            _asset("a-1", "普通说明.txt", "材料用于业务归档和一般说明。"),
            _asset("a-2", "主体关系.txt", "法定代表人和企业主体之间的登记关系已经列明。"),
        ],
    }
    result = await ContextualHybridRetriever.search(ctx, {})

    assert "dense" in result["channels"]
    assert result["dense_status"] == "used"
    assert any("dense" in row["channels"] for row in result["hits"])


def test_chunking_adds_attachment_context_without_unbounded_growth():
    asset = _asset(
        "a-1",
        "授权链路.txt",
        ("授权主体、被授权主体、品牌和有效期均已记录。" * 400),
        semantic_text="该附件是品牌授权链路材料，包含授权主体和有效期。",
    )
    chunks = ContextualHybridRetriever._chunks([asset])

    assert chunks
    assert len(chunks) <= ContextualHybridRetriever.MAX_CHUNKS_PER_ASSET
    assert all("资料：授权链路.txt" in chunk.contextual_text for chunk in chunks)
    assert all(len(chunk.contextual_text) <= 2200 for chunk in chunks)
