from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_product_entrypoint_preserves_optional_queue_and_grounding_composition():
    source = (ROOT / "ecomevo" / "product" / "__init__.py").read_text(encoding="utf-8")
    assert "QueueConversationStore" in source
    assert "FeedbackConversationStore" in source
    assert "grounded_analyzer" in source
    assert "ProductAnalyzer" in source
