from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.feedback_store import FEEDBACK_CATEGORIES, REVIEW_DECISIONS


FeedbackCategory = Literal[
    "factual_error",
    "missing_support",
    "wrong_rule",
    "stale_source",
    "evidence_conflict",
    "incorrect_evidence",
    "rule_not_applicable",
    "over_inference",
    "inappropriate_action",
    "stale_attachment",
    "unreliable_attachment",
    "other",
]
FeedbackImpact = Literal["answer_only", "decision_relevant", "action_blocking"]
TargetType = Literal["answer", "claim", "evidence", "action", "asset"]
ReviewDecision = Literal["acknowledged", "accepted_for_eval", "needs_followup", "dismissed"]


class FeedbackSubmit(BaseModel):
    assistant_message_id: str = Field(min_length=1, max_length=120)
    category: FeedbackCategory
    impact: FeedbackImpact
    target_type: TargetType = "answer"
    target_ref: str = Field(default="", max_length=240)
    explanation: str = Field(min_length=3, max_length=6000)
    proposed_correction: str = Field(default="", max_length=6000)


class FeedbackReview(BaseModel):
    decision: ReviewDecision
    note: str = Field(default="", max_length=4000)


def _authority() -> dict:
    return {
        "feedback_changes_production_authority": False,
        "feedback_changes_policy": False,
        "feedback_changes_routing": False,
        "accepted_for_eval_auto_promotes_to_gold_set": False,
    }


def install_feedback_routes(app: FastAPI, store, frontend: Path) -> None:
    """Install feedback/dispute surfaces without creating an authority backchannel."""

    @app.get("/api/feedback/capabilities")
    def feedback_capabilities():
        principal = current_principal()
        return {
            "can_submit": principal.can("operator"),
            "can_review": principal.can("admin"),
            "authority": _authority(),
        }

    @app.get("/api/conversations/{cid}/feedback/targets")
    def feedback_targets(cid: str, message_id: str = Query(min_length=1, max_length=120)):
        principal = current_principal()
        try:
            result = store.feedback_targets(
                cid,
                message_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "回复不存在") from exc
        result["can_submit"] = principal.can("operator")
        result["authority"] = _authority()
        return result

    @app.get("/api/conversations/{cid}/feedback")
    def feedback_list(cid: str, limit: int = Query(default=100, ge=1, le=200)):
        principal = current_principal()
        try:
            items = store.list_feedback(cid, tenant_id=principal.tenant_id, limit=limit)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        return {"count": len(items), "items": items, "authority": _authority()}

    @app.post("/api/conversations/{cid}/feedback")
    def feedback_submit(cid: str, req: FeedbackSubmit):
        principal = current_principal()
        try:
            item = store.submit_feedback(
                cid,
                req.assistant_message_id,
                submitted_by=principal.user_id,
                submitted_role=principal.role,
                category=req.category,
                impact=req.impact,
                target_type=req.target_type,
                target_ref=req.target_ref,
                explanation=req.explanation,
                proposed_correction=req.proposed_correction,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务或回复不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        item["authority"] = _authority()
        return item

    # Runtime namespace is admin-only under IdentityMiddleware. These endpoints can
    # review/export feedback but still cannot change production authority or source data.
    @app.get("/api/runtime/feedback/ui", include_in_schema=False)
    def feedback_admin_ui():
        return FileResponse(frontend / "feedback-admin.html")

    @app.get("/api/runtime/feedback")
    def feedback_admin_list(
        status: str | None = Query(default=None, max_length=64),
        category: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=200, ge=1, le=200),
    ):
        principal = current_principal()
        if category and category not in FEEDBACK_CATEGORIES:
            raise HTTPException(422, "反馈类型无效")
        valid_statuses = {"open", *REVIEW_DECISIONS}
        if status and status not in valid_statuses:
            raise HTTPException(422, "反馈状态无效")
        items = store.list_feedback_admin(
            tenant_id=principal.tenant_id,
            status=status,
            category=category,
            limit=limit,
        )
        return {"count": len(items), "items": items, "authority": _authority()}

    @app.get("/api/runtime/feedback/{feedback_id}/events")
    def feedback_admin_events(feedback_id: str):
        principal = current_principal()
        try:
            events = store.feedback_events(feedback_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "反馈不存在") from exc
        return {"items": events, "authority": _authority()}

    @app.get("/api/runtime/feedback/{feedback_id}/evaluation-sample")
    def feedback_evaluation_sample(feedback_id: str):
        principal = current_principal()
        try:
            return store.evaluation_sample(feedback_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "反馈不存在") from exc

    @app.get("/api/runtime/feedback/{feedback_id}")
    def feedback_admin_get(feedback_id: str):
        principal = current_principal()
        try:
            item = store.get_feedback(feedback_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "反馈不存在") from exc
        item["authority"] = _authority()
        return item

    @app.post("/api/runtime/feedback/{feedback_id}/review")
    def feedback_admin_review(feedback_id: str, req: FeedbackReview):
        principal = current_principal()
        try:
            item = store.review_feedback(
                feedback_id,
                req.decision,
                actor_user_id=principal.user_id,
                actor_role=principal.role,
                note=req.note,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "反馈不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        item["authority"] = _authority()
        return item
