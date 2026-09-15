from __future__ import annotations

from pathlib import Path

import pytest

from ecomevo.product.media import probe_media
from ecomevo.runtime.hybrid_retrieval import ContextualHybridRetriever


@pytest.mark.asyncio
async def test_hybrid_search_preserves_fact_beyond_bounded_chunk_window(tmp_path: Path, monkeypatch):
    for key in (
        "ECOMEVO_RETRIEVAL_EMBEDDING_BASE_URL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_MODEL",
        "ECOMEVO_RETRIEVAL_EMBEDDING_API_KEY",
        "ECOMEVO_RETRIEVAL_RERANK_URL",
        "ECOMEVO_RETRIEVAL_RERANK_MODEL",
        "ECOMEVO_RETRIEVAL_RERANK_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

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
    assert "stream_tail" in result["hits"][0]["channels"]
    assert any("sku-8899" in term.lower() for term in result["query_terms"])
    assert "SKU-8899" in result["hits"][0]["snippet"]
