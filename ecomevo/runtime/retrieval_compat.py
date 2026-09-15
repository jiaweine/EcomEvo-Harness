from __future__ import annotations

import re
from typing import Any

from . import tools as runtime_tools
from .hybrid_retrieval import ContextualHybridRetriever


_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,10}[-_:]?[A-Za-z0-9_-]{3,}(?![A-Za-z0-9])")


class StreamingTailRecallGuard:
    """Preserve the legacy guarantee that facts beyond display/search windows remain findable.

    Hybrid retrieval intentionally bounds contextual chunks for latency and memory. Some
    attachments can be much larger than those windows, so every search gets one final
    streaming pass over assets that are not already represented in the candidate set.
    This is a recall guard only: it is read-only and does not participate in authority.
    """

    @classmethod
    def _score(cls, query: str, matched: list[str]) -> float:
        identifiers = {
            value.upper()
            for value in _IDENTIFIER_RE.findall(str(query or ""))
            if any(ch.isdigit() for ch in value)
        }
        matched_upper = {str(value).upper() for value in matched}
        exact_identifier = bool(identifiers & matched_upper)
        # Keep the score in the same small magnitude as RRF while allowing an exact
        # business identifier (order/SKU/merchant id) found in the streamed tail to
        # compete with ordinary lexical passages.
        return round(0.012 + min(0.018, 0.003 * len(matched)) + (0.018 if exact_identifier else 0.0), 6)

    @classmethod
    async def augment(cls, result: dict[str, Any], ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        query = " ".join(args.get("keywords") or []) or str(ctx.get("text") or "")
        words = list(result.get("query_terms") or runtime_tools._query_terms(query))
        if not words:
            return result

        hits = [dict(row) for row in (result.get("hits") or []) if isinstance(row, dict)]
        present = {str(row.get("asset_id") or "") for row in hits}
        added = False
        for asset in ctx.get("assets") or []:
            aid = str(asset.get("id") or "")
            if not aid or aid in present:
                continue
            matched, snippet = runtime_tools._stream_text_hit(asset, words)
            if not matched:
                continue
            score = cls._score(query, matched)
            hits.append(
                {
                    "asset_id": aid,
                    "name": str(asset.get("name") or aid),
                    "score": score,
                    "snippet": snippet,
                    "matched": matched,
                    "channels": ["stream_tail"],
                    "passages": [{"ordinal": -1, "score": score, "snippet": snippet}],
                    "counter_candidate": ContextualHybridRetriever._counter_candidate(snippet, words),
                }
            )
            present.add(aid)
            added = True

        if not added:
            return result

        hits.sort(key=lambda row: (-float(row.get("score") or 0.0), str(row.get("asset_id") or "")))
        for rank, row in enumerate(hits, 1):
            row["rank"] = rank
        result = dict(result)
        result["hits"] = hits[: ContextualHybridRetriever.MAX_HITS]
        channels = list(result.get("channels") or [])
        if "stream_tail" not in channels:
            channels.append("stream_tail")
        result["channels"] = channels
        result["stream_tail_recall"] = True
        return result


def install_streaming_tail_recall_guard() -> None:
    if getattr(ContextualHybridRetriever, "_ecomevo_stream_tail_guard_installed", False):
        return
    original_search = ContextualHybridRetriever.search

    async def search(cls, ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        result = await original_search(ctx, args)
        return await StreamingTailRecallGuard.augment(result, ctx, args)

    ContextualHybridRetriever.search = classmethod(search)
    ContextualHybridRetriever._ecomevo_stream_tail_guard_installed = True
