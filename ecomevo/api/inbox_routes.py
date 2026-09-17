from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ecomevo.identity import current_principal
from ecomevo.product.queue_store import QUEUE_PRIORITIES


QueueView = Literal["all", "mine", "unassigned"]
QueueScene = Literal["product_governance", "merchant_review", "aftersales", "risk_review", "content_audit"]


class PriorityPatch(BaseModel):
    priority: Literal["low", "normal", "high", "urgent"]


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
