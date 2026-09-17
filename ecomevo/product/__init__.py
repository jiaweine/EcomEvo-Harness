from . import lifecycle_store as _lifecycle_store  # patch the base schema before guarded/tenant subclasses load
from .feedback_store import FeedbackConversationStore

try:
    from .queue_store import QueueConversationStore
except ImportError:  # #146 remains independently runnable before the Inbox branch lands.
    ConversationStore = FeedbackConversationStore
else:
    class ConversationStore(FeedbackConversationStore, QueueConversationStore):
        """Compose evidence-dispute and queue extensions over the shared tenant store."""

        pass

from .media import probe_media, extract_video_frames

try:
    from .grounded_analyzer import ProductAnalyzer
except ImportError:  # #146 remains independently runnable before claim grounding lands.
    from .analyzer import ProductAnalyzer

__all__ = ['ConversationStore', 'probe_media', 'extract_video_frames', 'ProductAnalyzer']
