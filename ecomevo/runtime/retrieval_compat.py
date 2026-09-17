from __future__ import annotations

import re
from typing import Any

from . import tools as runtime_tools
from .hybrid_retrieval import ContextualHybridRetriever


_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,10}[-_:]?[A-Za-z0-9_-]{3,}(?![A-Za-z0-9])")
_RECOVERY_CHANNELS = {"residual_index", "stream_tail"}


class StreamingTailRecallGuard:
    """Close and normalize recall gaps left by bounded hybrid candidate chunking.

    Hybrid retrieval intentionally caps contextual chunks for latency and memory. That
    cap is smaller than the persisted search index, so a relevant fact can live outside
    the neural/BM25 candidate window without the attachment itself being search-truncated.

    Recovery has two bounded stages for assets not already represented:
    1. residual_index: scan persisted search_text (bounded to the media SEARCH_LIMIT);
    2. stream_tail: only when that persisted index is itself truncated, scan the source
       text file with the legacy streaming reader.

    The compatibility layer is also the canonical recovery-policy/telemetry boundary.
    Earlier hybrid implementations may already emit a stream_tail candidate; those rows
    are normalized here, subjected to the same exact-ID precision rule, and reflected in
    stable top-level recovery telemetry.
    """

    @staticmethod
    def _query_identifiers(query: str) -> set[str]:
        return {
            value.upper()
            for value in _IDENTIFIER_RE.findall(str(query or ""))
            if any(ch.isdigit() for ch in value)
        }

    @classmethod
    def _qualifies(cls, query: str, matched: list[str]) -> bool:
        if not matched:
            return False
        identifiers = cls._query_identifiers(query)
        if not identifiers:
            return True
        matched_upper = {str(value).upper() for value in matched}
        # If the operator supplied a concrete order/SKU/merchant identifier, generic
        # domain words are not sufficient to resurrect a long attachment.
        return bool(identifiers & matched_upper)

    @classmethod
    def _score(cls, query: str, matched: list[str], *, streamed: bool = False) -> float:
        identifiers = cls._query_identifiers(query)
        matched_upper = {str(value).upper() for value in matched}
        exact_identifier = bool(identifiers & matched_upper)
        # Keep recovery scores in the same small magnitude as RRF. Exact business IDs
        # can compete with ordinary passages but stay below a strong multi-channel hit.
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
        if not cls._qualifies(query, matched):
            return [], ""

        # Center the excerpt on the strongest anchor. Exact business identifiers win;
        # otherwise prefer the longest matched term over a generic first token.
        anchors: list[tuple[int, int, str]] = []
        identifiers = cls._query_identifiers(query)
        for word in matched:
            value = str(word)
            if lowered.find(value.lower()) < 0:
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
    def _normalize_existing_recovery_hits(
        cls,
        hits: list[dict[str, Any]],
        query: str,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Apply one precision policy to recovery rows regardless of who emitted them."""
        normalized: list[dict[str, Any]] = []
        used_channels: set[str] = set()
        for row in hits:
            channels = {str(value) for value in (row.get("channels") or [])}
            recovery = channels & _RECOVERY_CHANNELS
            if recovery and not cls._qualifies(query, list(row.get("matched") or [])):
                # An older hybrid path may have recovered a long attachment from generic
                # terms. Drop it when the query contains an exact ID that is absent.
                continue
            used_channels.update(recovery)
            normalized.append(row)
        return normalized, used_channels

    @classmethod
    def _with_telemetry(
        cls,
        result: dict[str, Any],
        hits: list[dict[str, Any]],
        used_channels: set[str],
    ) -> dict[str, Any]:
        output = dict(result)
        output["hits"] = hits[: ContextualHybridRetriever.MAX_HITS]
        channels = list(output.get("channels") or [])
        for channel in ("residual_index", "stream_tail"):
            if channel in used_channels and channel not in channels:
                channels.append(channel)
        output["channels"] = channels
        # Stable booleans avoid forcing callers/tests/observability code to infer
        # recovery from per-hit channel arrays or handle missing schema keys.
        output["residual_recall"] = "residual_index" in used_channels
        output["stream_tail_recall"] = "stream_tail" in used_channels
        return output

    @classmethod
    async def augment(cls, result: dict[str, Any], ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        query = " ".join(args.get("keywords") or []) or str(ctx.get("text") or "")
        # Never use display-capped result[query_terms] for recall: the decisive business
        # identifier may appear after the first 12 display terms.
        words = runtime_tools._query_terms(query)
        raw_hits = [dict(row) for row in (result.get("hits") or []) if isinstance(row, dict)]
        hits, used_channels = cls._normalize_existing_recovery_hits(raw_hits, query)
        if not words:
            return cls._with_telemetry(result, hits, used_channels)

        present = {str(row.get("asset_id") or "") for row in hits}
        for asset in ctx.get("assets") or []:
            aid = str(asset.get("id") or "")
            if not aid or aid in present:
                continue

            matched, snippet = cls._residual_index_hit(asset, query, words)
            channel = "residual_index"
            if not matched and bool((asset.get("meta") or {}).get("search_truncated")):
                matched, snippet = runtime_tools._stream_text_hit(asset, words)
                if not cls._qualifies(query, matched):
                    matched, snippet = [], ""
                channel = "stream_tail"
            if not matched:
                continue

            cls._append_hit(hits, asset, query, words, matched, snippet, channel=channel)
            present.add(aid)
            used_channels.add(channel)

        hits.sort(key=lambda row: (-float(row.get("score") or 0.0), str(row.get("asset_id") or "")))
        for rank, row in enumerate(hits, 1):
            row["rank"] = rank
        return cls._with_telemetry(result, hits, used_channels)


def install_streaming_tail_recall_guard() -> None:
    if getattr(ContextualHybridRetriever, "_ecomevo_stream_tail_guard_installed", False):
        return
    original_search = ContextualHybridRetriever.search

    async def search(cls, ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        result = await original_search(ctx, args)
        return await StreamingTailRecallGuard.augment(result, ctx, args)

    ContextualHybridRetriever.search = classmethod(search)
    ContextualHybridRetriever._ecomevo_stream_tail_guard_installed = True
