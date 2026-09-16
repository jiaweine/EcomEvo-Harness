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
from .upload_security import validate_raster as _validate_raster


# Preserve established monkeypatch/test hooks without duplicating event persistence.
# application.emit remains the single durable event path; process-local queues are
# wake signals only and WebSocket delivery drains authoritative SQLite task_events.
_application.Image = Image
_application._validate_raster = _validate_raster

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
