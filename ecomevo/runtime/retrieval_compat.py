from __future__ import annotations

import re
from typing import Any

from . import tools as runtime_tools
from .hybrid_retrieval import ContextualHybridRetriever


_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,10}[-_:]?[A-Za-z0-9_-]{3,}(?![A-Za-z0-9])")


class StreamingTailRecallGuard:
    """Close recall gaps left by bounded hybrid candidate chunking.

    Hybrid retrieval intentionally caps contextual chunks for latency and memory. That
    cap is smaller than the persisted search index, so a relevant fact can live outside
    the neural/BM25 candidate window without the attachment itself being search-truncated.

    Recovery therefore has two bounded stages for assets not already represented:
    1. residual_index: scan the persisted search_text (bounded to the media SEARCH_LIMIT)
       and extract the strongest lexical/identifier-centered snippet;
    2. stream_tail: only when that persisted index is itself truncated, scan the source
       text file with the legacy streaming reader.

    Both stages are read-only recall guards. They never alter Verifier/Governance/Action
    authority and they run only after the normal hybrid/rerank path missed an asset.
    """

    @staticmethod
    def _query_identifiers(query: str) -> set[str]:
        return {
            value.upper()
            for value in _IDENTIFIER_RE.findall(str(query or ""))
            if any(ch.isdigit() for ch in value)
        }

    @classmethod
    def _score(cls, query: str, matched: list[str], *, streamed: bool = False) -> float:
        identifiers = cls._query_identifiers(query)
        matched_upper = {str(value).upper() for value in matched}
        exact_identifier = bool(identifiers & matched_upper)
        # Keep recovery scores in the same small magnitude as RRF. Exact business IDs
        # deserve enough weight to compete with ordinary passages, while residual hits
        # remain below a strong multi-channel hybrid match.
        base = 0.010 if not streamed else 0.009
        return round(base + min(0.018, 0.003 * len(matched)) + (0.018 if exact_identifier else 0.0), 6)

    @classmethod
    def _residual_index_hit(
        cls,
        asset: dict[str, Any],
        query: str,
        words: list[str],
    ) -> tuple[list[str], str]:
        raw = runtime_tools._asset_text(asset, 500000, search=True)
        if not raw or not words:
            return [], ""
        lowered = raw.lower()
        matched = [word for word in words if str(word).lower() in lowered]
        if not matched:
            return [], ""

        # Center the snippet on the strongest available anchor. Exact order/SKU/merchant
        # identifiers win; otherwise prefer the longest matched term rather than the first
        # generic domain token so the evidence excerpt is maximally discriminative.
        anchors: list[tuple[int, int, str]] = []
        identifiers = cls._query_identifiers(query)
        for word in matched:
            value = str(word)
            pos = lowered.find(value.lower())
            if pos < 0:
                continue
            identifier_priority = 1 if value.upper() in identifiers else 0
            anchors.append((identifier_priority, len(value), value))
        if not anchors:
            return matched[:12], raw[:520]
        _id_priority, _length, anchor = max(anchors, key=lambda item: (item[0], item[1]))
        pos = lowered.find(anchor.lower())
        start = max(0, pos - 180)
        return matched[:12], raw[start : start + 620]

    @classmethod
    def _append_hit(
        cls,
        hits: list[dict[str, Any]],
        asset: dict[str, Any],
        query: str,
        words: list[str],
        matched: list[str],
        snippet: str,
        *,
        channel: str,
    ) -> None:
        score = cls._score(query, matched, streamed=channel == "stream_tail")
        aid = str(asset.get("id") or "")
        hits.append(
            {
                "asset_id": aid,
                "name": str(asset.get("name") or aid),
                "score": score,
                "snippet": snippet,
                "matched": matched,
                "channels": [channel],
                "passages": [{"ordinal": -1, "score": score, "snippet": snippet}],
                "counter_candidate": ContextualHybridRetriever._counter_candidate(snippet, words),
            }
        )

    @classmethod
    async def augment(cls, result: dict[str, Any], ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        query = " ".join(args.get("keywords") or []) or str(ctx.get("text") or "")
        words = list(result.get("query_terms") or runtime_tools._query_terms(query))
        if not words:
            return result

        hits = [dict(row) for row in (result.get("hits") or []) if isinstance(row, dict)]
        present = {str(row.get("asset_id") or "") for row in hits}
        used_channels: set[str] = set()

        for asset in ctx.get("assets") or []:
            aid = str(asset.get("id") or "")
            if not aid or aid in present:
                continue

            matched, snippet = cls._residual_index_hit(asset, query, words)
            channel = "residual_index"
            if not matched and bool((asset.get("meta") or {}).get("search_truncated")):
                matched, snippet = runtime_tools._stream_text_hit(asset, words)
                channel = "stream_tail"
            if not matched:
                continue

            cls._append_hit(hits, asset, query, words, matched, snippet, channel=channel)
            present.add(aid)
            used_channels.add(channel)

        if not used_channels:
            return result

        hits.sort(key=lambda row: (-float(row.get("score") or 0.0), str(row.get("asset_id") or "")))
        for rank, row in enumerate(hits, 1):
            row["rank"] = rank
        result = dict(result)
        result["hits"] = hits[: ContextualHybridRetriever.MAX_HITS]
        channels = list(result.get("channels") or [])
        for channel in ("residual_index", "stream_tail"):
            if channel in used_channels and channel not in channels:
                channels.append(channel)
        result["channels"] = channels
        result["residual_recall"] = "residual_index" in used_channels
        result["stream_tail_recall"] = "stream_tail" in used_channels
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
