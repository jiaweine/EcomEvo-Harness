from __future__ import annotations

import asyncio
import json
import threading
import time
import weakref
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from .harness_optimizer import HarnessEvolutionOptimizer


_REPLAY_GROUP_LIMIT = 64
_T = TypeVar("_T")


@dataclass(slots=True)
class _GroupedReplayCase:
    domain: str
    trajectory_json: str
    created_at: float
    future: asyncio.Future[None]


@dataclass(slots=True)
class _LoopReplayGroup:
    queue: list[_GroupedReplayCase] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class BundledHarnessEvolutionOptimizer(HarnessEvolutionOptimizer):
    """Built-in Harness optimizer fast paths that preserve the base plugin contract.

    The public ``HarnessEvolutionOptimizer`` remains the compatibility surface. This
    subclass only adds optional methods used by the built-in Engine to keep phase-aligned
    SQLite work off the asyncio thread and to avoid unnecessary writer-slot acquisition
    and full optimizer snapshots on steady-state paths.
    """

    def __init__(self, db_path, *, sandbox=None):
        self._async_gate_lock = threading.RLock()
        self._async_gates: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()
        self._replay_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopReplayGroup
        ] = weakref.WeakKeyDictionary()
        self._skip_replay_record: ContextVar[bool] = ContextVar(
            f"bundled_harness_skip_replay_record_{id(self)}",
            default=False,
        )
        super().__init__(db_path, sandbox=sandbox)

    def _async_gate(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        with self._async_gate_lock:
            gate = self._async_gates.get(loop)
            if gate is None:
                gate = asyncio.Lock()
                self._async_gates[loop] = gate
            return gate

    async def _run_io(self, call: Callable[..., _T], /, *args, **kwargs) -> _T:
        """Run one Harness SQLite operation off-loop with unambiguous cancellation.

        Waiting for the async gate can be cancelled immediately because no database work
        has started. Once submitted to the worker, cancellation waits until the operation
        has committed or failed so a caller never observes an ambiguous late write.
        """
        loop = asyncio.get_running_loop()
        gate = self._async_gate(loop)
        async with gate:
            task = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError as cancelled:
                try:
                    await asyncio.shield(task)
                except Exception:
                    raise
                raise cancelled

    async def record_outcome_async(self, *args, **kwargs) -> list[dict[str, Any]]:
        return await self._run_io(self.record_outcome, *args, **kwargs)

    def _record_replay_case(self, *args, **kwargs) -> None:
        if self._skip_replay_record.get():
            return
        super()._record_replay_case(*args, **kwargs)

    async def _record_replay_case_grouped(
        self,
        domain: str,
        trajectory: dict[str, Any],
    ) -> None:
        """Durably coalesce phase-aligned replay evidence without blocking the loop."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        request = _GroupedReplayCase(
            domain=str(domain),
            trajectory_json=json.dumps(trajectory, ensure_ascii=False, default=str)[:24000],
            created_at=time.time(),
            future=future,
        )
        with self._async_gate_lock:
            group = self._replay_groups.get(loop)
            if group is None:
                group = _LoopReplayGroup()
                self._replay_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_replay_group(group))

        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            # Once queued, preserve the synchronous evidence contract: cancellation is
            # observable only after this replay case has committed or failed.
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    @staticmethod
    def _fail_replay_requests(
        requests: list[_GroupedReplayCase],
        error: BaseException,
    ) -> None:
        for request in requests:
            if not request.future.done():
                request.future.set_exception(error)

    def _stop_replay_group(self, group: _LoopReplayGroup) -> list[_GroupedReplayCase]:
        with self._async_gate_lock:
            queued = list(group.queue)
            group.queue.clear()
            group.scheduled = False
            group.worker = None
        return queued

    async def _flush_replay_group(self, group: _LoopReplayGroup) -> None:
        try:
            while True:
                # One scheduler turn lets phase-aligned proposals join the bounded batch
                # without adding a fixed latency window to isolated proposals.
                await asyncio.sleep(0)
                with self._async_gate_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    batch = list(group.queue[:_REPLAY_GROUP_LIMIT])
                    del group.queue[: len(batch)]

                persist_task = asyncio.create_task(
                    self._run_io(self._persist_replay_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    await asyncio.shield(persist_task)
                except asyncio.CancelledError as cancelled:
                    worker_cancelled = cancelled
                    try:
                        await asyncio.shield(persist_task)
                    except Exception as exc:
                        self._fail_replay_requests(batch, exc)
                        queued = self._stop_replay_group(group)
                        self._fail_replay_requests(
                            queued,
                            RuntimeError("harness replay worker cancelled before persistence"),
                        )
                        raise cancelled
                except Exception:
                    # A shared transaction must roll back as a unit. Retry requests one by
                    # one so an isolated SQLite/row failure does not poison valid peers.
                    for index, request in enumerate(batch):
                        if request.future.done():
                            continue
                        single_task = asyncio.create_task(
                            self._run_io(self._persist_replay_group, [request])
                        )
                        try:
                            await asyncio.shield(single_task)
                        except asyncio.CancelledError as cancelled:
                            try:
                                await asyncio.shield(single_task)
                            except Exception as exc:
                                request.future.set_exception(exc)
                            else:
                                request.future.set_result(None)
                            self._fail_replay_requests(
                                batch[index + 1 :],
                                RuntimeError(
                                    "harness replay worker cancelled before isolated persistence"
                                ),
                            )
                            queued = self._stop_replay_group(group)
                            self._fail_replay_requests(
                                queued,
                                RuntimeError("harness replay worker cancelled before persistence"),
                            )
                            raise cancelled
                        except Exception as exc:
                            request.future.set_exception(exc)
                        else:
                            request.future.set_result(None)
                    continue

                for request in batch:
                    if not request.future.done():
                        request.future.set_result(None)

                if worker_cancelled is not None:
                    queued = self._stop_replay_group(group)
                    self._fail_replay_requests(
                        queued,
                        RuntimeError("harness replay worker cancelled before persistence"),
                    )
                    raise worker_cancelled
        except asyncio.CancelledError:
            queued = self._stop_replay_group(group)
            self._fail_replay_requests(
                queued,
                RuntimeError("harness replay worker cancelled before persistence"),
            )
            raise

    def _persist_replay_group(self, batch: list[_GroupedReplayCase]) -> None:
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO harness_replay_cases(domain,trajectory_json,created_at) VALUES(?,?,?)",
                [
                    (request.domain, request.trajectory_json, request.created_at)
                    for request in batch
                ],
            )

    async def propose(
        self,
        domain: str,
        *,
        trajectory: dict[str, Any],
        tool_catalog: list[dict[str, Any]],
        reasoner=None,
    ) -> dict[str, Any] | None:
        """Persist replay evidence cheaply and avoid read-only writer reservations.

        Built-in replay evidence is phase-aligned across concurrent runs, so persist it
        through the loop-local group-commit queue before inspecting optimizer state. Each
        caller still waits for durable evidence before continuing, but SQLite work stays
        off-loop and up to 64 replay cases share one writer transaction.

        The base optimizer then remains the mutation authority. Existing-shadow proposals
        return from a deferred WAL read snapshot. If no shadow is visible, delegation to
        the base implementation retains its original bootstrap transaction, replay gate,
        and final shadow/current-component rechecks before candidate insertion.
        """
        await self._record_replay_case_grouped(domain, trajectory)
        with self._conn() as connection:
            connection.execute("BEGIN")
            if self._domain_initialized(connection, domain) and self._has_shadow(connection, domain):
                return None

        token = self._skip_replay_record.set(True)
        try:
            return await super().propose(
                domain,
                trajectory=trajectory,
                tool_catalog=tool_catalog,
                reasoner=reasoner,
            )
        finally:
            self._skip_replay_record.reset(token)

    def state_summary(self, domain: str) -> dict[str, Any]:
        """Return the post-proposal state required by RuntimeSummary in one small read.

        ``snapshot()`` decodes every component field and posterior statistic. The Engine
        only publishes active generations and shadow generations, so do not pay for the
        full representation on every completed task.
        """
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT kind,status,generation FROM harness_components "
                "WHERE domain=? AND status IN ('active','shadow') "
                "ORDER BY kind,status,generation",
                (domain,),
            ).fetchall()
        if not rows:
            # A normal Engine run initialized the domain during profile(). Keep this
            # optional helper robust for direct callers without changing bootstrap rules.
            snapshot = self.snapshot(domain)
            components = list(snapshot.get("components") or [])
        else:
            components = [dict(row) for row in rows]
        return {
            "active": {
                str(row["kind"]): int(row["generation"])
                for row in components
                if row.get("status") == "active"
            },
            "shadow": [
                {"kind": str(row["kind"]), "generation": int(row["generation"])}
                for row in components
                if row.get("status") == "shadow"
            ],
        }

    async def state_summary_async(self, domain: str) -> dict[str, Any]:
        return await self._run_io(self.state_summary, domain)
