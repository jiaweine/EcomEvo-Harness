"""Compatibility alias for the modular API implementation.

``ecomevo.api.app`` remains the canonical import/monkeypatch surface while the
production implementation lives in smaller modules.
"""
from __future__ import annotations

import sys
from PIL import Image

from ecomevo.evaluation import EvaluationCenter
from ecomevo.identity import IdentityMiddleware
from . import application as _application
from .evaluation_api import build_evaluation_router
from .policy_api import build_policy_router
from .policy_worker import PolicyAwareDurableConversationWorker
from .upload_security import validate_raster as _validate_raster


# Preserve established monkeypatch/test hooks without duplicating event persistence.
# application.emit remains the single durable event path; process-local queues are
# wake signals only and WebSocket delivery drains authoritative SQLite task_events.
_application.Image = Image
_application._validate_raster = _validate_raster

# The lifespan worker may execute long after the originating HTTP request and may be
# reclaimed by another process. Replace the pre-start worker with a tenant-aware wrapper
# that derives policy scope from the durable conversation row rather than request-local
# identity state. Core lease/execution behavior remains inherited unchanged.
if not isinstance(_application.job_worker, PolicyAwareDurableConversationWorker):
    _application.job_worker = PolicyAwareDurableConversationWorker(
        _application.store,
        _application.analyzer,
        _application.mcp,
        emit=_application.emit,
        wake=_application.wake,
        logger=_application.logger,
    )

if not getattr(_application.app.state, "policy_router_installed", False):
    _application.app.include_router(build_policy_router(_application.engine))
    _application.app.state.policy_router_installed = True

if not getattr(_application.app.state, "evaluation_router_installed", False):
    _application.evaluation_center = EvaluationCenter(_application.DATA_DIR / "evaluation.db")
    _application.app.include_router(
        build_evaluation_router(_application.evaluation_center, _application.FRONTEND)
    )
    _application.app.state.evaluation_router_installed = True

if not getattr(_application.app.state, "identity_middleware_installed", False):
    _application.app.add_middleware(IdentityMiddleware, store=_application.store)
    _application.app.state.identity_middleware_installed = True

sys.modules[__name__] = _application
