"""Compatibility alias for the modular API implementation.

``ecomevo.api.app`` remains the canonical import/monkeypatch surface while the
production implementation lives in smaller modules.
"""
from __future__ import annotations

import sys
from PIL import Image

from ecomevo.evaluation import EvaluationCenter
from ecomevo.identity import IdentityMiddleware
from ecomevo.product.release_readiness import ReleaseReadinessCenter
from . import application as _application
from .connection_routes import install_connection_routes
from .decision_export_routes import install_decision_export_routes
from .evaluation_api import build_evaluation_router
from .feedback_routes import install_feedback_routes
from .inbox_routes import install_inbox_routes
from .knowledge_routes import install_knowledge_routes
from .observability_routes import install_observability_routes
from .operator_activity_routes import install_operator_activity_routes
from .policy_api import build_policy_router
from .policy_workflow_routes import install_policy_workflow_routes
from .policy_worker import PolicyAwareDurableConversationWorker
from .release_readiness_routes import install_release_readiness_routes
from .routing_quality_routes import install_routing_quality_routes
from .shadow_environment_routes import install_shadow_environment_routes
from .skill_studio_routes import install_skill_studio_routes
from .upload_security import validate_raster as _validate_raster

_application.Image = Image
_application._validate_raster = _validate_raster

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

if not getattr(_application.app.state, "policy_workflow_routes_installed", False):
    _application.policy_workflow = install_policy_workflow_routes(
        _application.app,
        engine=_application.engine,
        frontend=_application.FRONTEND,
    )
    _application.app.state.policy_workflow_routes_installed = True

if not getattr(_application.app.state, "evaluation_router_installed", False):
    _application.evaluation_center = EvaluationCenter(_application.DATA_DIR / "evaluation.db")
    _application.app.include_router(
        build_evaluation_router(_application.evaluation_center, _application.FRONTEND)
    )
    _application.app.state.evaluation_router_installed = True

if not getattr(_application.app.state, "connection_routes_installed", False):
    install_connection_routes(
        _application.app,
        _application.mcp,
        _application.FRONTEND,
        _application.DATA_DIR,
    )
    _application.app.state.connection_routes_installed = True

if not getattr(_application.app.state, "inbox_routes_installed", False):
    install_inbox_routes(_application.app, _application.store, _application.FRONTEND)
    _application.app.state.inbox_routes_installed = True

if not getattr(_application.app.state, "feedback_routes_installed", False):
    install_feedback_routes(_application.app, _application.store, _application.FRONTEND)
    _application.app.state.feedback_routes_installed = True

if not getattr(_application.app.state, "operator_activity_routes_installed", False):
    _application.operator_activity_ledger = install_operator_activity_routes(
        _application.app,
        _application.store,
    )
    _application.app.state.operator_activity_routes_installed = True

if not getattr(_application.app.state, "observability_routes_installed", False):
    install_observability_routes(_application.app, _application.store, _application.FRONTEND)
    _application.app.state.observability_routes_installed = True

if not getattr(_application.app.state, "routing_quality_routes_installed", False):
    install_routing_quality_routes(
        _application.app,
        _application.store,
        _application.FRONTEND,
    )
    _application.app.state.routing_quality_routes_installed = True

if not getattr(_application.app.state, "shadow_environment_routes_installed", False):
    _application.shadow_environment = install_shadow_environment_routes(
        _application.app,
        frontend=_application.FRONTEND,
    )
    _application.app.state.shadow_environment_routes_installed = True

if not getattr(_application.app.state, "knowledge_routes_installed", False):
    _application.knowledge_source_store = install_knowledge_routes(
        _application.app,
        db_path=_application.DATA_DIR / "knowledge.db",
        frontend=_application.FRONTEND,
    )
    _application.app.state.knowledge_routes_installed = True

if not getattr(_application.app.state, "decision_export_routes_installed", False):
    _application.decision_export_center = install_decision_export_routes(
        _application.app,
        _application.store,
        _application.FRONTEND,
        _application.DATA_DIR / "decision_exports.db",
    )
    _application.app.state.decision_export_routes_installed = True

if not getattr(_application.app.state, "release_readiness_routes_installed", False):
    _application.release_readiness_center = ReleaseReadinessCenter(
        _application.DATA_DIR / "release_readiness.db",
        store=_application.store,
        evaluation_center=_application.evaluation_center,
        mcp_registry=_application.mcp,
        policy_store=_application.engine.policies,
    )
    install_release_readiness_routes(
        _application.app,
        center=_application.release_readiness_center,
        frontend=_application.FRONTEND,
    )
    _application.app.state.release_readiness_routes_installed = True

if not getattr(_application.app.state, "skill_studio_routes_installed", False):
    _application.skill_studio = install_skill_studio_routes(
        _application.app,
        db_path=_application.DATA_DIR / "skill_studio.db",
        engine=_application.engine,
        frontend=_application.FRONTEND,
    )
    _application.app.state.skill_studio_routes_installed = True

if not getattr(_application.app.state, "identity_middleware_installed", False):
    _application.app.add_middleware(IdentityMiddleware, store=_application.store)
    _application.app.state.identity_middleware_installed = True

sys.modules[__name__] = _application
