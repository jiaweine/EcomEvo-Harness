from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from ecomevo.models import BusinessAction

EmitFn = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
WakeFn = Callable[[str], None]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class DurableConversationWorker:
    """Execute persisted cognitive jobs with cross-process SQLite leases.

    FastAPI BackgroundTasks may call ``run_once`` as a latency optimization, but the
    durable queue is authoritative. A process crash leaves a leased ``running`` row;
    another worker can reclaim it after the job lease expires and resume from the
    immutable asset/message snapshot stored in the job payload.
    """

    def __init__(self, store, analyzer, mcp, *, emit: EmitFn, wake: WakeFn, logger: logging.Logger | None = None):
        self.store = store
        self.analyzer = analyzer
        self.mcp = mcp
        self.emit = emit
        self.wake = wake
        self.logger = logger or logging.getLogger(__name__)
        try:
            self.poll_seconds = float(os.environ.get("ECOMEVO_JOB_POLL_SECONDS", "1"))
        except (TypeError, ValueError):
            self.poll_seconds = 1.0
        self.poll_seconds = max(0.25, min(10.0, self.poll_seconds))
        try:
            self.lease_seconds = float(os.environ.get("ECOMEVO_JOB_LEASE_SECONDS", "120"))
        except (TypeError, ValueError):
            self.lease_seconds = 120.0
        self.lease_seconds = max(60.0, min(600.0, self.lease_seconds))
        self.worker_id = f"worker-{uuid.uuid4().hex[:12]}"

    def _load_assets(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for snapshot in payload.get("assets") or []:
            aid = str(snapshot.get("id") or "")
            if not aid:
                raise RuntimeError("durable job asset snapshot contains an empty id")
            row = self.store.get_asset(aid)
            path = Path(str(row.get("path") or ""))
            if not path.is_file():
                raise RuntimeError(f"durable job asset is unavailable: {aid}")
            expected = str(snapshot.get("sha256") or "")
            actual = str((row.get("meta") or {}).get("sha256") or "")
            if expected and actual != expected:
                raise RuntimeError(f"durable job asset metadata changed: {aid}")
            if expected and _file_sha256(path) != expected:
                raise RuntimeError(f"durable job asset content changed: {aid}")
            assets.append(row)
        return assets

    async def _renew(self, job: dict[str, Any], stop: asyncio.Event) -> None:
        cid = str(job["conversation_id"])
        token = str((job.get("payload") or {}).get("lease_token") or "")
        interval = max(10.0, min(30.0, self.lease_seconds / 3.0))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                job_ok = self.store.renew_job(job["id"], self.worker_id, self.lease_seconds)
                turn_ok = bool(token) and self.store.renew_or_restore_turn(cid, token, self.lease_seconds)
                if not job_ok or not turn_ok:
                    return

    async def _execute(self, job: dict[str, Any]) -> None:
        payload = job.get("payload") or {}
        cid = str(job["conversation_id"])
        token = str(payload.get("lease_token") or "")
        if not token or not self.store.renew_or_restore_turn(cid, token, self.lease_seconds):
            event = self.store.finish_job_failure(
                job["id"], worker_id=self.worker_id,
                message="本次处理没有完成",
                detail="任务执行权已发生变化，系统已停止旧任务以避免重复处理。",
            )
            if event:
                self.wake(cid)
            return

        stop_renew = asyncio.Event()
        renew_task = asyncio.create_task(self._renew(job, stop_renew))
        try:
            assets = await asyncio.to_thread(self._load_assets, payload)

            async def sink(event_type: str, event_payload: dict[str, Any]):
                await self.emit(cid, event_type, event_payload)

            result = await self.analyzer.run(
                text=str(payload.get("content") or ""),
                assets=assets,
                provider_key=str(payload.get("provider") or "auto"),
                sink=sink,
                domain_hint=str(payload.get("domain") or "") or None,
                history=list(payload.get("history") or []),
            )
            actions: list[BusinessAction] = []
            for raw in result.get("actions", []):
                action = BusinessAction(**raw)
                binding = self.mcp.action_binding(action.kind, {
                    "conversation_id": cid,
                    "session_id": result["session_id"],
                    "domain": result["domain"],
                    "verifier_score": result.get("runtime", {}).get("verifier_score", 0),
                    "action_kind": action.kind,
                    "action_id": action.action_id,
                    "risk_level": action.risk_level,
                })
                if binding:
                    action.payload.update(binding)
                actions.append(action)
            completed = self.store.finish_job_success(
                job["id"], worker_id=self.worker_id, session_id=result["session_id"],
                actions=actions, answer=result["answer"], result=result,
            )
            if completed:
                self.wake(cid)
        except Exception:
            self.logger.exception("durable conversation job failed: %s", job.get("id"))
            event = self.store.finish_job_failure(
                job["id"], worker_id=self.worker_id,
                message="本次处理没有完成",
                detail="服务执行异常，任务资料仍然保留；请重试，如持续失败请联系管理员。",
            )
            if event:
                self.wake(cid)
        finally:
            stop_renew.set()
            renew_task.cancel()
            try:
                await renew_task
            except (asyncio.CancelledError, Exception):
                pass
            self.store.release_turn(cid, token)

    async def run_once(self, job_id: str | None = None) -> bool:
        job = self.store.claim_job(self.worker_id, job_id=job_id, lease_seconds=self.lease_seconds)
        if not job:
            return False
        await self._execute(job)
        return True

    async def loop(self, stop: asyncio.Event) -> None:
        # Give request-local fast paths the first chance to claim freshly queued work.
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                break
            except asyncio.TimeoutError:
                pass
            while not stop.is_set():
                claimed = await self.run_once()
                if not claimed:
                    break
                await asyncio.sleep(0)
