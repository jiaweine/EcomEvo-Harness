from __future__ import annotations

from collections import deque
from typing import Any, Iterable


class RuntimeMemory:
    def __init__(self, max_items: int = 300):
        self.items = deque(maxlen=max_items)

    def add(self, item: dict[str, Any]):
        self.items.append(item)

    @staticmethod
    def _terms(value: Any) -> set[str]:
        if isinstance(value, (list, tuple, set)):
            text = " ".join(str(x) for x in value)
        else:
            text = str(value or "")
        return {token.strip().lower() for token in text.replace("/", " ").replace("|", " ").split() if token.strip()}

    def relevant(self, domain: str, limit: int = 6, query_terms: Iterable[str] = ()):
        rows = [x for x in reversed(self.items) if x.get("domain") == domain]
        query = {str(x).strip().lower() for x in query_terms if str(x).strip()}
        if not query:
            return rows[:limit]
        scored = []
        for recency, row in enumerate(rows):
            haystack = self._terms([row.get("goal", ""), *(row.get("risks") or [])])
            overlap = len(query & haystack)
            scored.append((overlap, -recency, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [row for _, _, row in scored[:limit]]
