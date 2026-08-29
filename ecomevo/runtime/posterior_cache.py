from __future__ import annotations

from typing import Any

from .adaptive_routing import AdaptiveRoutingStore


class CachedAdaptiveRoutingStore(AdaptiveRoutingStore):
    """Adaptive routing store with version-safe posterior factorization caching.

    The database remains the source of truth on every routing round. Only the expensive
    inverse/mean derived from an identical posterior A/b state is reused. The cache key
    contains the complete numeric posterior state, so writes from this process or another
    process naturally produce a miss without requiring an invalidation broadcast.
    """

    MAX_POSTERIOR_CACHE = 32

    def __init__(self, db_path):
        super().__init__(db_path)
        self._posterior_cache: dict[
            tuple[Any, ...], tuple[list[list[float]], list[float]]
        ] = {}
        self._posterior_cache_hits = 0
        self._posterior_cache_misses = 0

    @staticmethod
    def _posterior_cache_key(row: dict[str, Any]) -> tuple[Any, ...]:
        matrix = tuple(tuple(float(value) for value in values) for values in row["a"])
        vector = tuple(float(value) for value in row["b"])
        return (
            str(row.get("policy_key") or ""),
            int(row.get("samples") or 0),
            matrix,
            vector,
        )

    def _posterior_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        key = self._posterior_cache_key(row)
        with self._lock:
            cached = self._posterior_cache.get(key)
            if cached is not None:
                self._posterior_cache_hits += 1
                inverse, mean = cached
                return {**row, "inverse": inverse, "mean": mean}

        # Matrix inversion is CPU work. Keep it outside the cache lock so an unrelated
        # policy/domain can still read its cached posterior concurrently.
        computed = super()._posterior_from_row(row)
        inverse = computed["inverse"]
        mean = computed["mean"]
        with self._lock:
            existing = self._posterior_cache.get(key)
            if existing is not None:
                self._posterior_cache_hits += 1
                inverse, mean = existing
            else:
                self._posterior_cache_misses += 1
                self._posterior_cache[key] = (inverse, mean)
                while len(self._posterior_cache) > self.MAX_POSTERIOR_CACHE:
                    self._posterior_cache.pop(next(iter(self._posterior_cache)))
        return {**row, "inverse": inverse, "mean": mean}

    def posterior_cache_stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._posterior_cache),
                "hits": self._posterior_cache_hits,
                "misses": self._posterior_cache_misses,
            }
