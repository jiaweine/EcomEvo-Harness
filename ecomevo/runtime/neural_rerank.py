from __future__ import annotations

import math
import os
from typing import Any

import httpx

from .hybrid_retrieval import ContextualHybridRetriever


class NeuralEvidenceReranker:
    """Optional second-stage reranking for hybrid evidence retrieval.

    The endpoint contract intentionally follows the common cross-encoder rerank shape:
    request ``{model, query, documents, top_n}``, response ``results`` (or ``data``)
    with ``index`` and ``relevance_score``/``score``. This works with many enterprise
    gateways and self-hosted rerank services without binding the Runtime to one vendor.

    Reranking is quality-only. Any timeout, malformed response or unavailable endpoint
    returns the original hybrid order and cannot change Verifier/Governance authority.
    """

    MAX_DOCUMENTS = 12
    RRF_K = 60.0

    @classmethod
    def _config(cls) -> tuple[str, str, str] | None:
        url = os.environ.get("ECOMEVO_RETRIEVAL_RERANK_URL", "").strip()
        model = os.environ.get("ECOMEVO_RETRIEVAL_RERANK_MODEL", "").strip()
        key = os.environ.get("ECOMEVO_RETRIEVAL_RERANK_API_KEY", "").strip()
        if not (url and model):
            return None
        return url, model, key

    @staticmethod
    def _document(hit: dict[str, Any]) -> str:
        passages = hit.get("passages") or []
        passage_text = "\n".join(str(row.get("snippet") or "") for row in passages[:2] if isinstance(row, dict))
        value = f"资料：{hit.get('name') or hit.get('asset_id') or '业务资料'}\n{passage_text or hit.get('snippet') or ''}"
        return value[:2600]

    @classmethod
    async def _scores(cls, query: str, hits: list[dict[str, Any]]) -> tuple[list[float] | None, str]:
        config = cls._config()
        if config is None:
            return None, "not_configured"
        url, model, key = config
        candidates = hits[: cls.MAX_DOCUMENTS]
        if len(candidates) < 2:
            return None, "not_needed"
        try:
            timeout = max(2.0, min(30.0, float(os.environ.get("ECOMEVO_RETRIEVAL_RERANK_TIMEOUT", "10"))))
        except (TypeError, ValueError):
            timeout = 10.0
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "query": str(query or "")[:4000],
            "documents": [cls._document(hit) for hit in candidates],
            "top_n": len(candidates),
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            rows = body.get("results") or body.get("data") or []
            if not isinstance(rows, list):
                return None, "invalid_response"
            scores = [0.0] * len(candidates)
            seen: set[int] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    index = int(row.get("index"))
                    score = float(row.get("relevance_score", row.get("score")))
                except (TypeError, ValueError):
                    continue
                if index < 0 or index >= len(candidates) or index in seen or not math.isfinite(score):
                    continue
                seen.add(index)
                scores[index] = score
            # We request top_n == candidate count. A partial response is treated as
            # malformed instead of silently assigning zero to missing candidates and
            # turning transport/provider behavior into a fake ranking signal.
            if len(seen) != len(candidates):
                return None, "invalid_response"
            return scores, "used"
        except Exception:
            return None, "fallback"

    @classmethod
    def _fuse_ranks(cls, hits: list[dict[str, Any]], neural_scores: list[float]) -> list[dict[str, Any]]:
        count = min(len(hits), len(neural_scores), cls.MAX_DOCUMENTS)
        if count < 2:
            return hits
        neural_order = [
            idx for idx, score in sorted(enumerate(neural_scores[:count]), key=lambda item: (-item[1], item[0]))
            if math.isfinite(score)
        ]
        neural_rank = {idx: rank for rank, idx in enumerate(neural_order, 1)}
        fused: list[tuple[float, int, dict[str, Any]]] = []
        for idx, hit in enumerate(hits[:count]):
            original_rank = idx + 1
            score = 1.0 / (cls.RRF_K + original_rank)
            if idx in neural_rank:
                score += 1.35 / (cls.RRF_K + neural_rank[idx])
            row = dict(hit)
            row["rerank_score"] = round(float(neural_scores[idx]), 6)
            row["rerank_fusion"] = round(score, 8)
            channels = list(row.get("channels") or [])
            if "neural_rerank" not in channels:
                channels.append("neural_rerank")
            row["channels"] = channels
            fused.append((score, original_rank, row))
        fused.sort(key=lambda item: (-item[0], item[1]))
        reranked = [row for _score, _rank, row in fused]
        reranked.extend(dict(hit) for hit in hits[count:])
        for rank, row in enumerate(reranked, 1):
            row["rank"] = rank
        return reranked

    @classmethod
    async def rerank(cls, query: str, hits: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        if not hits:
            return hits, "not_needed"
        scores, status = await cls._scores(query, hits)
        if scores is None:
            return hits, status
        return cls._fuse_ranks(hits, scores), status


def install_neural_evidence_reranker() -> None:
    if getattr(ContextualHybridRetriever, "_ecomevo_neural_rerank_installed", False):
        return
    original_search = ContextualHybridRetriever.search

    async def search(cls, ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        result = await original_search(ctx, args)
        query = " ".join(args.get("keywords") or []) or str(ctx.get("text") or "")
        hits, status = await NeuralEvidenceReranker.rerank(query, list(result.get("hits") or []))
        result["hits"] = hits
        result["rerank_status"] = status
        if status == "used":
            channels = list(result.get("channels") or [])
            if "neural_rerank" not in channels:
                channels.append("neural_rerank")
            result["channels"] = channels
            result["strategy"] = "contextual_hybrid_rrf_neural_rerank_mmr_v1"
        return result

    ContextualHybridRetriever.search = classmethod(search)
    ContextualHybridRetriever._ecomevo_neural_rerank_installed = True
