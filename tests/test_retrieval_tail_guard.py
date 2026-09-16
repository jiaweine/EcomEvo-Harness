from __future__ import annotations

from pathlib import Path

import pytest

from ecomevo.product.media import SEARCH_LIMIT, probe_media
from ecomevo.runtime.hybrid_retrieval import ContextualHybridRetriever


def _clear_remote_retrieval(monkeypatch) -> None:
    for key in (
        "ECOMEVO_RETRIEVAL_EMBEDDING_BASE_URL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_MODEL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_API_KEY",
        "ECOMEVO_RETRIEVAL_RERANK_URL",
        "ECOMEVO_RETRIEVAL_RERANK_MODEL",
        "ECOMEVO_RETRIEVAL_RERANK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_hybrid_search_preserves_fact_beyond_chunk_budget_inside_search_index(tmp_path: Path, monkeypatch):
    _clear_remote_retrieval(monkeypatch)

    content = "x" * 30000 + "\n商品ID SKU-8899 宣传治愈问题"
    path = tmp_path / "long.txt"
    path.write_text(content, encoding="utf-8")
    asset = {
        "id": "long",
        "name": "long.txt",
        "mime": "text/plain",
        "path": str(path),
        "meta": probe_media(path, "text/plain"),
    }

    result = await ContextualHybridRetriever.search(
        {"text": "核对 SKU-8899 的治愈宣称", "assets": [asset]},
        {},
    )

    assert result["hits"]
    assert result["hits"][0]["asset_id"] == "long"
    assert "residual_index" in result["hits"][0]["channels"]
    assert result["residual_recall"] is True
    assert result["stream_tail_recall"] is False
    assert any("sku-8899" in term.lower() for term in result["query_terms"])
    assert "SKU-8899" in result["hits"][0]["snippet"]


@pytest.mark.asyncio
async def test_hybrid_search_streams_source_after_persisted_index_limit(tmp_path: Path, monkeypatch):
    _clear_remote_retrieval(monkeypatch)

    content = "x" * (SEARCH_LIMIT + 12000) + "\n订单号 ORDER-TAIL-9917 发生破损退款"
    path = tmp_path / "very-long.txt"
    path.write_text(content, encoding="utf-8")
    asset = {
        "id": "very-long",
        "name": "very-long.txt",
        "mime": "text/plain",
        "path": str(path),
        "meta": probe_media(path, "text/plain"),
    }
    assert asset["meta"]["search_truncated"] is True

    result = await ContextualHybridRetriever.search(
        {"text": "核对 ORDER-TAIL-9917 的破损退款", "assets": [asset]},
        {},
    )

    assert result["hits"]
    assert result["hits"][0]["asset_id"] == "very-long"
    assert "stream_tail" in result["hits"][0]["channels"]
    assert result["stream_tail_recall"] is True
    assert "ORDER-TAIL-9917" in result["hits"][0]["snippet"]


@pytest.mark.asyncio
async def test_residual_recall_does_not_resurrect_generic_text_when_requested_id_is_absent(tmp_path: Path, monkeypatch):
    _clear_remote_retrieval(monkeypatch)

    content = "x" * 30000 + "\n该订单已发起退款，等待售后处理"
    path = tmp_path / "generic-order.txt"
    path.write_text(content, encoding="utf-8")
    asset = {
        "id": "generic-order",
        "name": "generic-order.txt",
        "mime": "text/plain",
        "path": str(path),
        "meta": probe_media(path, "text/plain"),
    }

    result = await ContextualHybridRetriever.search(
        {"text": "核对 ORDER-NOT-HERE-7788 的退款", "assets": [asset]},
        {},
    )

    assert result["hits"] == []
