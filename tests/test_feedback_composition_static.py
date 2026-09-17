from pathlib import Path


def test_feedback_product_entry_composes_optional_extensions():
    source = Path('ecomevo/product/__init__.py').read_text(encoding='utf-8')
    assert 'from .feedback_store import FeedbackConversationStore' in source
    assert 'from .queue_store import QueueConversationStore' in source
    assert 'class ConversationStore(FeedbackConversationStore, QueueConversationStore)' in source
    assert 'from .grounded_analyzer import ProductAnalyzer' in source
    assert 'except ImportError' in source
