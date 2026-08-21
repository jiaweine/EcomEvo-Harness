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

EmitFn = Callable[
    [str, str, dict[str, Any], str | None, str | None],
    Awaitable[dict[str, Any] | None],
]
WakeFn = Callable[[str], None]


class _JobLeaseLost(RuntimeError):
    """Internal control flow: another worker now owns this durable job."""


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
        self.renew_interval_seconds = max(10.0, min(30.0, self.lease_seconds / 3.0))
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

    async def _renew(
        self,
        job: dict[str, Any],
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        cid = str(job["conversation_id"])
        token = str((job.get("payload") or {}).get("lease_token") or "")
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.renew_interval_seconds)
                return
            except asyncio.TimeoutError:
                pass
            try:
                job_ok = self.store.renew_job(job["id"], self.worker_id, self.lease_seconds)
                if not job_ok:
                    lease_lost.set()
                    return
                turn_ok = bool(token) and self.store.renew_or_restore_turn(
                    cid, token, self.lease_seconds
                )
                if not turn_ok:
                    lease_lost.set()
                    return
            except Exception:
                # If ownership cannot be re-established, continuing provider/tool work
                # would be unsafe even when the database failure is transient.
                self.logger.exception(
                    "durable conversation job lease renewal failed: %s", job.get("id")
                )
                lease_lost.set()
                return

    async def _execute(self, job: dict[str, Any]) -> None:
        payload = job.get("payload") or {}
        cid = str(job["conversation_id"])
        token = str(payload.get("lease_token") or "")
        # Fence a stale in-memory claim before doing provider or tool work.
        try:
            job_owned = self.store.renew_job(job["id"], self.worker_id, self.lease_seconds)
        except Exception:
            self.logger.exception(
                "durable conversation job initial ownership check failed: %s", job.get("id")
            )
            return
        if not job_owned:
            self.logger.warning("durable conversation job ownership changed before start: %s", job.get("id"))
            return
        try:
            turn_owned = bool(token) and self.store.renew_or_restore_turn(
                cid, token, self.lease_seconds
            )
        except Exception:
            self.logger.exception(
                "durable conversation turn ownership check failed: %s", job.get("id")
            )
            return
        if not turn_owned:
            event = self.store.finish_job_failure(
                job["id"], worker_id=self.worker_id,
                message="本次处理没有完成",
                detail="任务执行权已发生变化，系统已停止旧任务以避免重复处理。",
            )
            if event:
                self.wake(cid)
            return

        stop_renew = asyncio.Event()
        lease_lost = asyncio.Event()
        renew_task = asyncio.create_task(self._renew(job, stop_renew, lease_lost))
        analysis_task: asyncio.Task | None = None
        lease_watch: asyncio.Task | None = None
        try:
            assets = await asyncio.to_thread(self._load_assets, payload)

            async def sink(event_type: str, event_payload: dict[str, Any]):
                if lease_lost.is_set():
                    raise _JobLeaseLost(job["id"])
                event = await self.emit(
                    cid, event_type, event_payload, job["id"], self.worker_id
                )
                if not event:
                    lease_lost.set()
                    raise _JobLeaseLost(job["id"])
                return event

            analysis_task = asyncio.create_task(
                self.analyzer.run(
                    text=str(payload.get("content") or ""),
                    assets=assets,
                    provider_key=str(payload.get("provider") or "auto"),
                    sink=sink,
                    domain_hint=str(payload.get("domain") or "") or None,
                    history=list(payload.get("history") or []),
                )
            )
            lease_watch = asyncio.create_task(lease_lost.wait())
            await asyncio.wait(
                {analysis_task, lease_watch},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lease_lost.is_set():
                if not analysis_task.done():
                    analysis_task.cancel()
                try:
                    await analysis_task
                except (asyncio.CancelledError, Exception):
                    pass
                raise _JobLeaseLost(job["id"])
            lease_watch.cancel()
            try:
                await lease_watch
            except asyncio.CancelledError:
                pass
            result = await analysis_task
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
            else:
                raise _JobLeaseLost(job["id"])
        except _JobLeaseLost:
            self.logger.warning("durable conversation job lease lost; stale work stopped: %s", job.get("id"))
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
            for task in (lease_watch, renew_task, analysis_task):
                if task and not task.done():
                    task.cancel()
                if task:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

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
                try:
                    claimed = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A transient claim/store error must not permanently kill the
                    # process-level durable worker loop.
                    self.logger.exception("durable conversation worker claim failed")
                    break
                if not claimed:
                    break
                await asyncio.sleep(0)
