from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ecomevo.product import ConversationStore, ProductAnalyzer, extract_video_frames, probe_media
from ecomevo.providers import ProviderRegistry
from ecomevo.runtime import EcomEvoEngine
from ecomevo.runtime.mcp import MCPRegistry
from .durable_jobs import DurableConversationWorker
from .upload_security import (
    RASTER_MIMES as _RASTER_MIMES,
    file_sha256 as _file_sha256,
    normalize_upload_type as _normalize_upload_type,
    public_asset as _public_asset,
    safe_download_name as _safe_download_name,
    upload_limit as _upload_limit,
    validate_uploaded_file as _validate_uploaded_file,
)

DATA_DIR = Path(os.environ.get("ECOMEVO_DATA", Path.cwd() / "outputs" / "runtime"))
FRONTEND = Path(str(files("frontend")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
store = ConversationStore(DATA_DIR / "product.db", DATA_DIR / "assets")
providers = ProviderRegistry()
mcp = MCPRegistry()
engine = EcomEvoEngine(DATA_DIR / "runtime.db", mcp=mcp, model_gateway=providers)
analyzer = ProductAnalyzer(engine, providers, asset_meta_writer=store.patch_asset_meta)
queues: dict[str, list[asyncio.Queue]] = {}
logger = logging.getLogger(__name__)

try:
    WS_POLL_SECONDS = float(os.environ.get("ECOMEVO_WS_POLL_SECONDS", "2"))
except (TypeError, ValueError):
    WS_POLL_SECONDS = 2.0
WS_POLL_SECONDS = max(0.5, min(15.0, WS_POLL_SECONDS))
WS_HEARTBEAT_SECONDS = 15.0


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Own the durable worker for exactly one ASGI application lifespan."""
    stop = asyncio.Event()
    task = asyncio.create_task(job_worker.loop(stop), name="ecomevo-durable-worker")
    application.state.durable_worker_stop = stop
    application.state.durable_worker_task = task
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("durable worker failed while the application was shutting down")
        application.state.durable_worker_task = None


app = FastAPI(
    title="EcomEvo 商业决策工作台 API",
    description="面向商品治理、商家审核、售后与风险核查的对话式多模态决策服务。",
    version="1.0.0",
    lifespan=lifespan,
)
cors_origins = [x.strip() for x in os.environ.get("ECOMEVO_CORS_ORIGINS", "").split(",") if x.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "authorization"],
    )


@app.middleware("http")
async def product_security_headers(request, response_next):
    response = await response_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    return response


app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")

Scene = Literal["product_governance", "merchant_review", "aftersales", "risk_review", "content_audit"]


class ConversationCreate(BaseModel):
    title: str = Field(default="新的业务任务", max_length=120)
    scene: Scene = "product_governance"

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("任务名称不能为空")
        return value


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    scene: Scene | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("任务名称不能为空")
        return value


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    asset_ids: list[str] = Field(default_factory=list, max_length=30)
    provider: str = Field(default="auto", max_length=40)

    @field_validator("content")
    @classmethod
    def non_blank_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value

    @field_validator("asset_ids")
    @classmethod
    def unique_assets(cls, value):
        return list(dict.fromkeys(value))


class ActionDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=2000)


class AssetScopePatch(BaseModel):
    active: bool
    reason: str = Field(default="", max_length=500)


def wake(cid: str) -> None:
    for queue in list(queues.get(cid, [])):
        try:
            queue.put_nowait(True)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(True)
            except asyncio.QueueFull:
                pass


async def emit(
    cid: str,
    event_type: str,
    payload: dict[str, Any],
    job_id: str | None = None,
    worker_id: str | None = None,
):
    if job_id is not None or worker_id is not None:
        if not job_id or not worker_id:
            return None
        event = store.add_job_event(job_id, worker_id, event_type, payload)
    else:
        event = store.add_event(cid, event_type, payload)
    if event:
        wake(str(event["conversation_id"]))
    return event


job_worker = DurableConversationWorker(store, analyzer, mcp, emit=emit, wake=wake, logger=logger)


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


@app.get("/api/product")
def product_info():
    return {
        "name": "EcomEvo 商业决策工作台",
        "subtitle": "把商品、商家、订单与多媒体证据放进一个任务里，直接完成核对、判定和后续处理。",
        "scenes": [
            {"key": "product_governance", "name": "商品治理", "desc": "商品信息、主图、详情、资质和风险声明交叉核对"},
            {"key": "merchant_review", "name": "商家审核", "desc": "主体、资质、授权、历史风险与关联信息复核"},
            {"key": "aftersales", "name": "售后判责", "desc": "订单、物流、沟通记录和用户举证统一判定"},
            {"key": "risk_review", "name": "风险核查", "desc": "交易、账户、商品与履约异常信号综合复核"},
            {"key": "content_audit", "name": "内容审核", "desc": "图片、视频、文案与商品事实一致性检查"},
        ],
        "accepted": ["图片", "视频", "音频", "PDF", "Word", "Excel", "CSV/JSON", "日志与文本"],
        "side_effect_policy": "高影响操作必须人工确认",
    }


@app.get("/healthz", include_in_schema=False)
def liveness():
    """Minimal unauthenticated liveness probe for containers and orchestrators."""
    return {"status": "ok"}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "product": "EcomEvo 商业决策工作台",
        "providers_configured": sum(1 for row in providers.list() if row.get("configured") and row["key"] not in {"auto", "demo"}),
        "mcp_connections": len(mcp.servers),
        "durable_jobs": store.job_counts(),
    }


@app.get("/api/providers")
def provider_list():
    return providers.list()


@app.get("/api/runtime")
def runtime_info():
    return {
        "plugins": engine.plugins.describe(),
        "tools": engine.tools.describe(),
        "event_store": {"append_only": True, "hash_chain": True, "checkpoint": True, "rollback": True, "fork_ready": True},
        "planner": {"adaptive": True, "parallel_tool_composition": True, "recursive_review": True, "cost_gate": True, "learned_checks": engine.planner.evolution_state()},
        "recovery": {"verify_before_finish": True, "rollback_replan": True, "failure_driven_evolution": True, "sandbox_replay": True, "regression_gate": True},
        "execution": {"durable_jobs": True, "cross_process_lease": True, "immutable_asset_snapshot": True, "jobs": store.job_counts()},
        "mcp": mcp.list(),
        "evolution_patches": engine.events.list_patches(10),
    }


@app.get("/api/evolution")
def evolution(limit: int = Query(default=30, ge=1, le=100)):
    return engine.events.list_patches(limit)


@app.get("/api/runtime/sessions/{session_id}/events")
def runtime_events(session_id: str):
    if not engine.events.has_session(session_id):
        raise HTTPException(404, "运行记录不存在")
    return [row.model_dump() for row in engine.events.list_events(session_id)]


@app.get("/api/conversations")
def conversation_list(limit: int = Query(default=40, ge=1, le=100)):
    return store.list_conversations(limit)


@app.post("/api/conversations")
def conversation_create(req: ConversationCreate):
    return store.create_conversation(req.title, req.scene)


@app.patch("/api/conversations/{cid}")
def conversation_patch(cid: str, req: ConversationPatch):
    try:
        current = store.get_conversation(cid)
        if req.scene is not None and req.scene != current["scene"] and store.has_messages(cid):
            raise HTTPException(409, "已有对话内容的任务不能修改业务场景，请新建任务")
        return store.update_conversation(cid, title=req.title, scene=req.scene)
    except KeyError:
        raise HTTPException(404, "任务不存在")


@app.get("/api/conversations/{cid}")
def conversation_get(cid: str):
    try:
        conversation = store.get_conversation(cid)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    if store.recover_interrupted_turn(cid):
        wake(cid)
    if store.recover_stale_actions(cid):
        wake(cid)
    message_count = store.count_messages(cid)
    return {
        **conversation,
        "messages": store.list_messages(cid, limit=200),
        "message_count": message_count,
        "history_truncated": message_count > 200,
        "assets": [_public_asset(row) for row in store.list_assets(cid, include_excluded=True)],
        "events": store.list_events(cid, limit=600),
        "actions": store.list_actions(cid),
        "busy": store.has_active_turn(cid),
    }


@app.post("/api/assets")
async def asset_upload(file: UploadFile = File(...), conversation_id: str = Form(...)):
    try:
        store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    if store.has_active_turn(conversation_id):
        raise HTTPException(409, "当前任务正在处理中，请在本轮完成后再追加资料")
    existing = store.list_assets(conversation_id, include_excluded=True)
    if len(existing) >= 120:
        raise HTTPException(409, "单个任务最多保留 120 份资料，请新建任务或整理现有资料")
    task_bytes = sum(int(row.get("size") or 0) for row in existing)
    task_cap = 2 * 1024 * 1024 * 1024
    if task_bytes >= task_cap:
        raise HTTPException(413, "当前任务资料总量已达到上限")
    filename = file.filename or "upload.bin"
    suffix, mime = _normalize_upload_type(filename, file.content_type or "")
    tmp = store.asset_dir / f"{uuid.uuid4().hex}{suffix}"
    frame_dir = store.asset_dir / f"{tmp.stem}_frames"
    limit = min(_upload_limit(mime, suffix), task_cap - task_bytes)
    size = 0
    digest = hashlib.sha256()
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"单个文件不能超过 {limit // 1024 // 1024}MB")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "不能上传空文件")
        await asyncio.to_thread(_validate_uploaded_file, tmp, mime, suffix)
        meta = await asyncio.to_thread(probe_media, tmp, mime)
        meta["sha256"] = digest.hexdigest()
        if mime.startswith("video/"):
            frames = await asyncio.to_thread(extract_video_frames, tmp, frame_dir, 4)
            meta["keyframes"] = frames
            meta["keyframe_sha256"] = {
                frame: await asyncio.to_thread(_file_sha256, frame)
                for frame in frames if Path(frame).is_file()
            }
        row = store.add_asset(
            conversation_id, name=_safe_download_name(filename), mime=mime,
            path=str(tmp), size=size, meta=meta,
        )
        return _public_asset(row)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        if frame_dir.exists():
            shutil.rmtree(frame_dir, ignore_errors=True)
        raise


def _verified_asset(asset_id: str) -> dict[str, Any]:
    try:
        row = store.get_asset(asset_id)
    except KeyError:
        raise HTTPException(404, "资料不存在")
    path = Path(row["path"])
    if not path.is_file():
        raise HTTPException(410, "资料文件已不可用")
    expected = str((row.get("meta") or {}).get("sha256") or "")
    if expected and _file_sha256(path) != expected:
        raise HTTPException(409, "资料内容指纹校验失败，请重新上传")
    return row


@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: str):
    row = _verified_asset(asset_id)
    return FileResponse(row["path"], media_type=row["mime"], filename=_safe_download_name(row["name"]), content_disposition_type="attachment")


@app.get("/api/assets/{asset_id}/preview/{index}")
def asset_preview(asset_id: str, index: int = 0):
    row = _verified_asset(asset_id)
    meta = row.get("meta") or {}
    frames = meta.get("keyframes", [])
    if frames:
        index = max(0, min(index, len(frames) - 1))
        frame = Path(frames[index])
        if not frame.is_file():
            raise HTTPException(410, "预览文件已不可用")
        expected = str((meta.get("keyframe_sha256") or {}).get(str(frame)) or "")
        if expected and _file_sha256(frame) != expected:
            raise HTTPException(409, "预览内容指纹校验失败")
        return FileResponse(frame, media_type="image/jpeg")
    if str(row.get("mime", "")) in _RASTER_MIMES:
        return FileResponse(row["path"], media_type=row["mime"])
    raise HTTPException(404, "没有可用预览")


@app.patch("/api/assets/{asset_id}/scope")
async def asset_scope(asset_id: str, req: AssetScopePatch):
    try:
        current = store.get_asset(asset_id)
    except KeyError:
        raise HTTPException(404, "资料不存在")
    cid = current.get("conversation_id")
    if cid and store.has_active_turn(cid):
        raise HTTPException(409, "任务正在处理，结果返回后再调整资料范围")
    try:
        row = store.set_asset_active(asset_id, req.active, req.reason)
    except KeyError:
        raise HTTPException(404, "资料不存在")
    if cid:
        await emit(cid, "asset.scope_updated", {
            "asset_id": asset_id,
            "active": bool(row["active"]),
            "reason": row.get("excluded_reason") or "",
        })
    return _public_asset(row)


@app.delete("/api/assets/{asset_id}")
async def asset_delete(asset_id: str):
    try:
        current = store.get_asset(asset_id)
    except KeyError:
        raise HTTPException(404, "资料不存在")
    cid = current.get("conversation_id")
    if cid and store.has_active_turn(cid):
        raise HTTPException(409, "任务正在处理，结果返回后再删除资料")
    try:
        row = store.delete_asset_if_unreferenced(asset_id)
    except KeyError:
        raise HTTPException(404, "资料不存在")
    if row is None:
        raise HTTPException(409, "该资料已经进入历史消息、证据或动作记录，不能物理删除；可以排除后续分析以保留审计链")
    if cid:
        await emit(cid, "asset.deleted", {"asset_id": asset_id, "name": row["name"]})
    return {"status": "deleted", "asset_id": asset_id, "name": row["name"]}


@app.post("/api/conversations/{cid}/messages")
async def conversation_message(cid: str, req: ChatRequest, background_tasks: BackgroundTasks):
    try:
        conversation = store.get_conversation(cid)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    if req.provider not in {"auto", "demo"} and req.provider not in providers.providers:
        raise HTTPException(422, "未知模型服务")
    prior_messages = store.list_messages(cid, limit=12)
    for aid in req.asset_ids:
        try:
            row = store.bind_asset(aid, cid)
        except KeyError:
            raise HTTPException(404, f"资料不存在：{aid}")
        if row is None:
            raise HTTPException(409, "不能引用其他任务中的资料")
        if not row.get("active", True):
            raise HTTPException(409, f"资料已排除后续分析：{row['name']}；如需使用请先重新启用")
        if not Path(row["path"]).is_file():
            raise HTTPException(410, f"资料文件已不可用：{row['name']}")

    task_assets = store.list_assets(cid, include_excluded=False)
    lease = store.claim_turn(cid)
    if lease is None:
        raise HTTPException(409, "当前任务正在处理上一条消息，请在结果返回后继续")
    try:
        assets: list[dict[str, Any]] = []
        unavailable: list[str] = []
        integrity_failed: list[str] = []
        for row in task_assets:
            path = Path(row["path"])
            if not path.is_file():
                unavailable.append(row["name"])
                continue
            expected = str((row.get("meta") or {}).get("sha256") or "")
            if expected:
                actual = await asyncio.to_thread(_file_sha256, path)
                if actual != expected:
                    integrity_failed.append(row["name"])
                    continue
            assets.append(row)
        if integrity_failed:
            raise HTTPException(409, "资料内容指纹校验失败，请重新上传：" + "、".join(integrity_failed[:5]))
        history = [{"role": row.get("role"), "content": row.get("content")} for row in prior_messages]
        asset_snapshot = [
            {"id": row["id"], "sha256": str((row.get("meta") or {}).get("sha256") or ""), "name": row.get("name")}
            for row in assets
        ]
        user, accepted, job = store.accept_message_job(
            cid,
            lease_token=lease,
            content=req.content,
            asset_ids=req.asset_ids,
            provider=req.provider,
            domain=conversation["scene"],
            history=history,
            asset_snapshot=asset_snapshot,
        )
        wake(cid)
    except Exception:
        store.release_turn(cid, lease)
        raise
    if unavailable:
        await emit(cid, "notice", {"title": "部分历史资料已不可用", "detail": "、".join(unavailable[:5]) + " 已从本轮核对中排除。"})

    # Latency optimization only: the durable DB queue remains the source of truth.
    # If this process exits here, another worker reclaims the persisted job after lease expiry.
    background_tasks.add_task(job_worker.run_once, job["id"])
    return {"status": "accepted", "message": user, "job_id": job["id"], "event_id": accepted["id"]}


@app.get("/api/conversations/{cid}/actions")
def action_list(cid: str, status: str | None = None):
    try:
        store.get_conversation(cid)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    if store.recover_stale_actions(cid):
        wake(cid)
    return store.list_actions(cid, status)


@app.post("/api/actions/{action_id}/decision")
async def action_decide(action_id: str, req: ActionDecision):
    try:
        action = store.get_action(action_id)
    except KeyError:
        raise HTTPException(404, "操作不存在")
    if req.decision == "reject":
        completed = store.transition_action_with_event(action_id, "proposed", "rejected", {"operator_note": req.note})
        if completed is None:
            raise HTTPException(409, "该操作已经处理过")
        row, _ = completed
        wake(action["conversation_id"])
        return row

    decision = engine.sandbox.validate_action(action["side_effect"], confirmed=True)
    if not decision.allowed:
        raise HTTPException(409, decision.reason)
    payload_patch = {"operator_note": req.note, "execution_mode": "awaiting_dispatch"}
    approved = store.transition_action_with_event(action_id, "proposed", "approved", payload_patch)
    if approved is None:
        raise HTTPException(409, "该操作已经处理过")
    claimed, _ = approved
    wake(action["conversation_id"])
    mcp_server = claimed["payload"].get("mcp_server")
    mcp_tool = claimed["payload"].get("mcp_tool")
    try:
        if mcp_server and mcp_tool:
            result = await mcp.call_tool(mcp_server, mcp_tool, claimed["payload"].get("arguments", {}))
            payload_patch.update({"execution_mode": "mcp", "execution_result": result, "execution_outcome": "confirmed"})
            status = "executed"
        else:
            payload_patch.update({
                "execution_mode": "simulation",
                "execution_result": {"simulated": True, "message": "已完成本地演示，不会改变真实业务系统状态"},
                "execution_outcome": "simulated",
            })
            status = "simulated"
    except httpx.TransportError as exc:
        logger.exception("business action result is uncertain: %s", action_id)
        row, _ = store.update_action_with_event(action_id, "uncertain", {
            "execution_error": "与业务系统通信中断，暂无法确认下游是否已执行；请先核对业务系统结果，不要直接重复操作。",
            "execution_outcome": "unknown",
        })
        wake(action["conversation_id"])
        raise HTTPException(502, "业务系统响应中断，当前操作结果暂无法确认，请先核对实际业务状态") from exc
    except Exception as exc:
        logger.exception("business action execution failed: %s", action_id)
        row, _ = store.update_action_with_event(action_id, "failed", {"execution_error": "下游业务服务明确返回执行失败", "execution_outcome": "failed"})
        wake(action["conversation_id"])
        raise HTTPException(502, "业务操作执行失败") from exc
    row, _ = store.update_action_with_event(action_id, status, payload_patch)
    wake(action["conversation_id"])
    return row


@app.websocket("/ws/conversations/{cid}")
async def conversation_ws(ws: WebSocket, cid: str, after_id: int = 0):
    await ws.accept()
    try:
        store.get_conversation(cid)
    except KeyError:
        await ws.close(code=4404)
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    queues.setdefault(cid, []).append(queue)
    try:
        latest_rows = store.list_events(cid, limit=1)
        durable_latest = int(latest_rows[-1]["id"]) if latest_rows else 0
        requested_after = max(0, int(after_id or 0))
        start_after = min(requested_after, durable_latest)
        history = store.list_events(cid, after_id=start_after, limit=600) if start_after else store.list_events(cid, limit=600)
        cutoff = start_after
        for event in history:
            event_id = int(event.get("id", 0) or 0)
            if event_id <= cutoff:
                continue
            await ws.send_json(event)
            cutoff = max(cutoff, event_id)
        last_heartbeat = time.monotonic()
        while True:
            pending = store.list_events(cid, after_id=cutoff, limit=200)
            if pending:
                for event in pending:
                    event_id = int(event.get("id", 0) or 0)
                    if event_id <= cutoff:
                        continue
                    await ws.send_json(event)
                    cutoff = max(cutoff, event_id)
                continue
            try:
                # Process-local queues are wake signals only. SQLite task_events remains
                # the authoritative cross-worker ordering source and is always drained above.
                await asyncio.wait_for(queue.get(), timeout=WS_POLL_SECONDS)
                continue
            except asyncio.TimeoutError:
                recovered = store.recover_interrupted_turn(cid)
                if recovered:
                    event_id = int(recovered.get("id", 0) or 0)
                    if event_id > cutoff:
                        await ws.send_json(recovered)
                        cutoff = event_id
                    continue
                now = time.monotonic()
                if now - last_heartbeat >= WS_HEARTBEAT_SECONDS:
                    await ws.send_json({"type": "heartbeat", "conversation_id": cid, "after_id": cutoff})
                    last_heartbeat = now
    except WebSocketDisconnect:
        pass
    finally:
        if queue in queues.get(cid, []):
            queues[cid].remove(queue)
        if not queues.get(cid):
            queues.pop(cid, None)
