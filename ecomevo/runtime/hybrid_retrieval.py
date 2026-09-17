from __future__ import annotations

import math
import os
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from . import tools as runtime_tools


_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,10}[-_:]?[A-Za-z0-9_-]{3,}(?![A-Za-z0-9])")
_NEGATION_RE = re.compile(r"(?:无|未|没有|并无|不存在|否认|不涉及|未发现|未见|不是|并非|不属于|正常)")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{1,}")


@dataclass
class RetrievalChunk:
    asset_id: str
    name: str
    text: str
    contextual_text: str
    terms: list[str]
    asset: dict[str, Any]
    ordinal: int


class ContextualHybridRetriever:
    """Evidence retrieval with exact, BM25 and optional dense channels.

    The default path is fully local and deterministic. When an explicit embedding
    service is configured, dense retrieval is fused with exact and contextual BM25
    using weighted Reciprocal Rank Fusion (RRF). A diversity pass reduces duplicate
    passages before the best passage(s) are aggregated back to an attachment.

    This component is retrieval-only: it never changes Verifier/Governance authority.
    """

    RRF_K = 60.0
    MAX_TOTAL_CHUNKS = 48
    MAX_CHUNKS_PER_ASSET = 10
    CHUNK_CHARS = 1400
    CHUNK_OVERLAP = 180
    MAX_HITS = 8
    _embedding_cache: "OrderedDict[str, list[float]]" = OrderedDict()
    _embedding_cache_max = 512

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    @classmethod
    def _tokens(cls, text: str, limit: int = 4200) -> list[str]:
        value = cls._normalize(text)
        tokens: list[str] = []
        for token in _LATIN_RE.findall(value):
            token = token.lower()
            if token not in runtime_tools.STOP_TERMS:
                tokens.append(token)
                if len(tokens) >= limit:
                    return tokens
        for segment in _CJK_RE.findall(value):
            if len(segment) <= 12 and segment not in runtime_tools.STOP_TERMS:
                tokens.append(segment)
            for width in (3, 2):
                for idx in range(max(0, len(segment) - width + 1)):
                    gram = segment[idx : idx + width]
                    if gram not in runtime_tools.STOP_TERMS:
                        tokens.append(gram)
                    if len(tokens) >= limit:
                        return tokens
        return tokens[:limit]

    @classmethod
    def _split(cls, raw: str) -> list[str]:
        raw = str(raw or "").strip()
        if not raw:
            return []
        if len(raw) <= cls.CHUNK_CHARS:
            return [raw]
        chunks: list[str] = []
        start = 0
        while start < len(raw) and len(chunks) < cls.MAX_CHUNKS_PER_ASSET:
            end = min(len(raw), start + cls.CHUNK_CHARS)
            if end < len(raw):
                boundary = max(raw.rfind(mark, start + 520, end) for mark in ("\n", "。", "；", ";", "！", "？"))
                if boundary > start + 520:
                    end = boundary + 1
            chunk = raw[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(raw):
                break
            start = max(start + 1, end - cls.CHUNK_OVERLAP)
        return chunks

    @classmethod
    def _chunks(cls, assets: list[dict[str, Any]]) -> list[RetrievalChunk]:
        rows: list[RetrievalChunk] = []
        for asset in assets or []:
            raw = runtime_tools._asset_text(asset, 500000, search=True)
            if not raw:
                continue
            meta = asset.get("meta") or {}
            semantic = re.sub(r"\s+", " ", str(meta.get("semantic_text") or "")).strip()[:320]
            name = str(asset.get("name") or asset.get("id") or "业务资料")
            kind = str(meta.get("kind") or asset.get("mime") or "file")
            prefix = f"资料：{name}；类型：{kind}。"
            if semantic:
                prefix += f"附件事实摘要：{semantic}。"
            for ordinal, chunk in enumerate(cls._split(raw)):
                contextual = f"{prefix}\n{chunk}"[:2200]
                rows.append(
                    RetrievalChunk(
                        asset_id=str(asset.get("id") or ""),
                        name=name,
                        text=chunk,
                        contextual_text=contextual,
                        terms=cls._tokens(contextual),
                        asset=asset,
                        ordinal=ordinal,
                    )
                )
                if len(rows) >= cls.MAX_TOTAL_CHUNKS:
                    return rows
        return rows

    @staticmethod
    def _idf(total: int, df: int) -> float:
        return math.log(1.0 + (total - df + 0.5) / (df + 0.5))

    @classmethod
    def _bm25_scores(cls, query_terms: list[str], chunks: list[RetrievalChunk]) -> list[float]:
        if not query_terms or not chunks:
            return [0.0] * len(chunks)
        counters = [Counter(chunk.terms) for chunk in chunks]
        lengths = [max(1, sum(counter.values())) for counter in counters]
        avg_len = sum(lengths) / max(1, len(lengths))
        df = {term: sum(1 for counter in counters if counter.get(term, 0)) for term in set(query_terms)}
        k1, b = 1.35, 0.72
        out: list[float] = []
        for counter, length in zip(counters, lengths):
            score = 0.0
            for term in query_terms:
                tf = counter.get(term, 0)
                if not tf:
                    continue
                denom = tf + k1 * (1.0 - b + b * length / avg_len)
                score += cls._idf(len(chunks), max(1, df.get(term, 0))) * (tf * (k1 + 1.0)) / denom
            out.append(score)
        return out

    @classmethod
    def _exact_scores(cls, query: str, words: list[str], chunks: list[RetrievalChunk]) -> list[float]:
        identifiers = {x.upper() for x in _IDENTIFIER_RE.findall(query) if any(ch.isdigit() for ch in x)}
        out: list[float] = []
        for chunk in chunks:
            text = chunk.contextual_text.lower()
            score = 0.0
            for word in words:
                if word and word.lower() in text:
                    score += 1.0 + min(1.4, len(word) / 8.0)
            upper = chunk.contextual_text.upper()
            score += 4.0 * sum(1 for identifier in identifiers if identifier in upper)
            if query.strip() and cls._normalize(query) in cls._normalize(chunk.contextual_text):
                score += 4.0
            out.append(score)
        return out

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        a_norm = math.sqrt(sum(a * a for a in left))
        b_norm = math.sqrt(sum(b * b for b in right))
        if not a_norm or not b_norm:
            return 0.0
        return dot / (a_norm * b_norm)

    @classmethod
    def _embedding_config(cls) -> tuple[str, str, str] | None:
        base = os.environ.get("ECOMEVO_RETRIEVAL_EMBEDDING_BASE_URL", "").strip().rstrip("/")
        model = os.environ.get("ECOMEVO_RETRIEVAL_EMBEDDING_MODEL", "").strip()
        key = os.environ.get("ECOMEVO_RETRIEVAL_EMBEDDING_API_KEY", "").strip()
        if not (base and model and key):
            return None
        return base, model, key

    @classmethod
    def _cache_get(cls, key: str) -> list[float] | None:
        value = cls._embedding_cache.get(key)
        if value is not None:
            cls._embedding_cache.move_to_end(key)
        return value

    @classmethod
    def _cache_put(cls, key: str, vector: list[float]) -> None:
        cls._embedding_cache[key] = vector
        cls._embedding_cache.move_to_end(key)
        while len(cls._embedding_cache) > cls._embedding_cache_max:
            cls._embedding_cache.popitem(last=False)

    @classmethod
    async def _dense_scores(cls, query: str, chunks: list[RetrievalChunk]) -> tuple[list[float] | None, str]:
        config = cls._embedding_config()
        if config is None:
            return None, "not_configured"
        base, model, key = config
        import hashlib

        docs = [chunk.contextual_text[:2000] for chunk in chunks]
        cache_keys = [hashlib.sha256(f"{model}\0{text}".encode("utf-8")).hexdigest() for text in docs]
        vectors: list[list[float] | None] = [cls._cache_get(cache_key) for cache_key in cache_keys]
        missing_indexes = [idx for idx, vector in enumerate(vectors) if vector is None]
        query_key = hashlib.sha256(f"{model}\0Q\0{query}".encode("utf-8")).hexdigest()
        query_vector = cls._cache_get(query_key)
        payload_inputs: list[str] = []
        payload_slots: list[int | str] = []
        if query_vector is None:
            payload_inputs.append(query[:4000])
            payload_slots.append("query")
        for idx in missing_indexes:
            payload_inputs.append(docs[idx])
            payload_slots.append(idx)
        try:
            if payload_inputs:
                timeout = max(3.0, min(30.0, float(os.environ.get("ECOMEVO_RETRIEVAL_EMBEDDING_TIMEOUT", "12"))))
                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{base}/embeddings",
                        headers=headers,
                        json={"model": model, "input": payload_inputs},
                    )
                response.raise_for_status()
                data = response.json().get("data") or []
                ordered = sorted(data, key=lambda row: int(row.get("index", 0)))
                if len(ordered) != len(payload_inputs):
                    return None, "invalid_response"
                for slot, row in zip(payload_slots, ordered):
                    vector = [float(x) for x in (row.get("embedding") or [])]
                    if not vector:
                        return None, "invalid_response"
                    if slot == "query":
                        query_vector = vector
                        cls._cache_put(query_key, vector)
                    else:
                        vectors[int(slot)] = vector
                        cls._cache_put(cache_keys[int(slot)], vector)
            if query_vector is None or any(vector is None for vector in vectors):
                return None, "invalid_response"
            return [cls._cosine(query_vector, vector or []) for vector in vectors], "used"
        except Exception:
            return None, "fallback"

    @staticmethod
    def _rank(scores: list[float]) -> list[int]:
        return [idx for idx, score in sorted(enumerate(scores), key=lambda item: (-item[1], item[0])) if score > 0]

    @classmethod
    def _rrf(cls, channels: list[tuple[str, float, list[float]]], count: int) -> tuple[list[float], list[set[str]]]:
        fused = [0.0] * count
        seen_channels = [set() for _ in range(count)]
        for name, weight, scores in channels:
            for rank, idx in enumerate(cls._rank(scores), 1):
                fused[idx] += weight / (cls.RRF_K + rank)
                seen_channels[idx].add(name)
        return fused, seen_channels

    @staticmethod
    def _authority(asset: dict[str, Any]) -> float:
        meta = asset.get("meta") or {}
        try:
            explicit = float(meta.get("source_authority"))
            return max(0.0, min(1.0, explicit))
        except (TypeError, ValueError):
            pass
        if meta.get("trusted_remote") or meta.get("authoritative"):
            return 1.0
        return 0.88

    @classmethod
    def _counter_candidate(cls, snippet: str, words: list[str]) -> bool:
        value = str(snippet or "")
        for word in words[:16]:
            for match in re.finditer(re.escape(word), value, flags=re.I):
                window = value[max(0, match.start() - 16) : match.end() + 18]
                if _NEGATION_RE.search(window):
                    return True
        return False

    @classmethod
    def _diversify(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        pending = list(rows)
        while pending and len(selected) < cls.MAX_HITS:
            if not selected:
                selected.append(pending.pop(0))
                continue
            best_idx, best_score = 0, -1e9
            for idx, row in enumerate(pending):
                row_terms = set(cls._tokens(row.get("snippet") or "", 500))
                redundancy = 0.0
                for chosen in selected:
                    chosen_terms = set(cls._tokens(chosen.get("snippet") or "", 500))
                    union = row_terms | chosen_terms
                    sim = len(row_terms & chosen_terms) / len(union) if union else 0.0
                    redundancy = max(redundancy, sim)
                mmr = 0.82 * float(row.get("score") or 0.0) - 0.18 * redundancy
                if mmr > best_score:
                    best_score = mmr
                    best_idx = idx
            selected.append(pending.pop(best_idx))
        return selected

    @classmethod
    async def search(cls, ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        query = " ".join(args.get("keywords") or []) or str(ctx.get("text") or "")
        words = runtime_tools._query_terms(query)
        chunks = cls._chunks(list(ctx.get("assets") or []))
        if not chunks or not words:
            return {
                "hits": [],
                "query_terms": words[:12],
                "strategy": "contextual_hybrid_rrf_mmr_v1",
                "channels": [],
                "dense_status": "not_used",
            }

        exact = cls._exact_scores(query, words, chunks)
        bm25 = cls._bm25_scores(cls._tokens(query, 160), chunks)
        dense, dense_status = await cls._dense_scores(query, chunks)
        channels: list[tuple[str, float, list[float]]] = [
            ("exact", 1.35, exact),
            ("contextual_bm25", 1.0, bm25),
        ]
        if dense is not None:
            channels.append(("dense", 1.15, dense))
        fused, channel_membership = cls._rrf(channels, len(chunks))

        by_asset: dict[str, dict[str, Any]] = {}
        for idx, chunk in enumerate(chunks):
            if fused[idx] <= 0:
                continue
            score = fused[idx] * (0.90 + 0.10 * cls._authority(chunk.asset))
            row = by_asset.setdefault(
                chunk.asset_id,
                {
                    "asset_id": chunk.asset_id,
                    "name": chunk.name,
                    "score": 0.0,
                    "snippet": "",
                    "matched": [],
                    "channels": set(),
                    "passages": [],
                },
            )
            matched = [word for word in words if word.lower() in chunk.contextual_text.lower()][:8]
            passage = {
                "ordinal": chunk.ordinal,
                "score": round(score, 6),
                "snippet": chunk.text[:620],
            }
            row["passages"].append(passage)
            row["channels"].update(channel_membership[idx])
            for word in matched:
                if word not in row["matched"]:
                    row["matched"].append(word)
            if score > row["score"]:
                row["score"] = score
                row["snippet"] = chunk.text[:620]

        # Preserve the previous large-text tail fallback when bounded search text was truncated.
        for asset in ctx.get("assets") or []:
            aid = str(asset.get("id") or "")
            if aid in by_asset:
                continue
            matched, snippet = runtime_tools._stream_text_hit(asset, words)
            if matched:
                by_asset[aid] = {
                    "asset_id": aid,
                    "name": str(asset.get("name") or aid),
                    "score": 0.001,
                    "snippet": snippet,
                    "matched": matched,
                    "channels": {"stream_tail"},
                    "passages": [{"ordinal": -1, "score": 0.001, "snippet": snippet}],
                }

        rows = sorted(by_asset.values(), key=lambda row: (-float(row["score"]), row["asset_id"]))
        for row in rows:
            row["passages"] = sorted(row["passages"], key=lambda item: -float(item["score"]))[:2]
            row["channels"] = sorted(row["channels"])
            row["score"] = round(float(row["score"]), 6)
            row["counter_candidate"] = cls._counter_candidate(row.get("snippet") or "", words)
        rows = cls._diversify(rows)
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank

        return {
            "hits": rows,
            "query_terms": words[:12],
            "strategy": "contextual_hybrid_rrf_mmr_v1",
            "channels": [name for name, _weight, _scores in channels],
            "dense_status": dense_status,
        }


def install_hybrid_evidence_search() -> None:
    if getattr(runtime_tools.EvidenceSearchTool, "_ecomevo_hybrid_installed", False):
        return
    original = runtime_tools.EvidenceSearchTool.execute

    async def execute(self, ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await ContextualHybridRetriever.search(ctx, args)
        except Exception:
            # Retrieval quality upgrades are read-only. Any local/remote retrieval
            # failure must degrade to the proven deterministic implementation rather
            # than breaking the task or changing an authority decision.
            return await original(self, ctx, args)

    runtime_tools.EvidenceSearchTool.execute = execute
    runtime_tools.EvidenceSearchTool._ecomevo_hybrid_installed = True
