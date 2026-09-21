from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ecomevo.identity import current_principal
from ecomevo.product.queue_store import CollaborationConflict, QUEUE_PRIORITIES


QueueView = Literal["all", "mine", "unassigned"]
QueueScene = Literal["product_governance", "merchant_review", "aftersales", "risk_review", "content_audit"]


class PriorityPatch(BaseModel):
    priority: Literal["low", "normal", "high", "urgent"]


class CollaborationCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class CollaborationTargetCreate(BaseModel):
    target_user_id: str = Field(min_length=1, max_length=240)
    note: str = Field(default="", max_length=2000)


def _summary(items: list[dict]) -> dict:
    states: dict[str, int] = {}
    assigned = 0
    urgent = 0
    for row in items:
        state = str(row.get("queue_state") or "ready")
        states[state] = states.get(state, 0) + 1
        assigned += int(bool(row.get("owner_user_id")))
        urgent += int(row.get("queue_priority") == "urgent")
    return {
        "states": states,
        "assigned": assigned,
        "unassigned": len(items) - assigned,
        "urgent": urgent,
    }


def install_inbox_routes(app: FastAPI, store, frontend: Path) -> None:
    """Install operator queue routes without creating a second authority layer.

    IdentityMiddleware supplies tenant isolation/RBAC. GET routes are viewer-safe;
    claim/release/priority mutations inherit the global operator requirement.
    None of these routes can approve or execute a BusinessAction.
    """

    @app.get("/api/inbox/ui", include_in_schema=False)
    def inbox_ui():
        return FileResponse(frontend / "inbox.html")

    @app.get("/api/inbox")
    def inbox_list(
        view: QueueView = Query(default="all"),
        scene: QueueScene | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        principal = current_principal()
        items = store.list_inbox(
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
            view=view,
            scene=scene,
            limit=limit,
        )
        return {
            "view": view,
            "scene": scene,
            "current_user": principal.user_id,
            "can_collaborate": principal.can("operator"),
            "count": len(items),
            "summary": _summary(items),
            "items": items,
            "authority": {
                "assignment_grants_approval": False,
                "priority_changes_runtime_routing": False,
            },
        }

    @app.get("/api/inbox/{cid}")
    def inbox_get(cid: str):
        principal = current_principal()
        try:
            return store.get_inbox_item(cid, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc

    @app.post("/api/inbox/{cid}/claim")
    def inbox_claim(cid: str):
        principal = current_principal()
        try:
            item = store.claim_conversation(
                cid,
                principal.user_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        if item is None:
            raise HTTPException(409, "任务已被其他同事认领，请刷新队列")
        return item

    @app.delete("/api/inbox/{cid}/claim")
    def inbox_release(cid: str):
        principal = current_principal()
        try:
            return store.release_claim(
                cid,
                principal.user_id,
                tenant_id=principal.tenant_id,
                allow_override=principal.can("admin"),
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except PermissionError as exc:
            raise HTTPException(409, "只能释放自己认领的任务") from exc

    @app.patch("/api/inbox/{cid}/priority")
    def inbox_priority(cid: str, req: PriorityPatch):
        principal = current_principal()
        if req.priority not in QUEUE_PRIORITIES:
            raise HTTPException(422, "优先级无效")
        try:
            return store.update_queue_priority(
                cid,
                req.priority,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc


    @app.get("/api/inbox/{cid}/collaboration")
    def inbox_collaboration(cid: str):
        principal = current_principal()
        try:
            return store.list_collaboration(
                cid,
                tenant_id=principal.tenant_id,
                current_user_id=principal.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc

    @app.put("/api/inbox/{cid}/watch")
    def inbox_watch(cid: str):
        principal = current_principal()
        try:
            return store.watch_conversation(
                cid,
                principal.user_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/inbox/{cid}/watch")
    def inbox_unwatch(cid: str):
        principal = current_principal()
        try:
            return store.unwatch_conversation(
                cid,
                principal.user_id,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/inbox/{cid}/comments")
    def inbox_comment(cid: str, req: CollaborationCommentCreate):
        principal = current_principal()
        try:
            return store.add_collaboration_comment(
                cid,
                principal.user_id,
                req.body,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/inbox/{cid}/review-requests")
    def inbox_review_request(cid: str, req: CollaborationTargetCreate):
        principal = current_principal()
        try:
            return store.request_task_review(
                cid,
                principal.user_id,
                req.target_user_id,
                note=req.note,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/inbox/{cid}/handoffs")
    def inbox_handoff_request(cid: str, req: CollaborationTargetCreate):
        principal = current_principal()
        try:
            return store.request_task_handoff(
                cid,
                principal.user_id,
                req.target_user_id,
                note=req.note,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except CollaborationConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def resolve_handoff(cid: str, request_id: int, decision: Literal["accept", "decline", "cancel"]):
        principal = current_principal()
        try:
            return store.resolve_task_handoff(
                cid,
                request_id,
                principal.user_id,
                decision,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "交接请求不存在") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except CollaborationConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/inbox/{cid}/handoffs/{request_id}/accept")
    def inbox_handoff_accept(cid: str, request_id: int):
        return resolve_handoff(cid, request_id, "accept")

    @app.post("/api/inbox/{cid}/handoffs/{request_id}/decline")
    def inbox_handoff_decline(cid: str, request_id: int):
        return resolve_handoff(cid, request_id, "decline")

    @app.post("/api/inbox/{cid}/handoffs/{request_id}/cancel")
    def inbox_handoff_cancel(cid: str, request_id: int):
        return resolve_handoff(cid, request_id, "cancel")
