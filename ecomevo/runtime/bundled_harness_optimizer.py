from __future__ import annotations

import asyncio
import threading
import weakref
from contextvars import ContextVar
from typing import Any, Callable, TypeVar

from .harness_optimizer import HarnessEvolutionOptimizer


_T = TypeVar("_T")


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

    async def propose(
        self,
        domain: str,
        *,
        trajectory: dict[str, Any],
        tool_catalog: list[dict[str, Any]],
        reasoner=None,
    ) -> dict[str, Any] | None:
        """Avoid taking the writer slot when an established domain already has a shadow.

        The base optimizer records the replay case and then opens ``BEGIN IMMEDIATE``
        before checking whether another coordinate is already under validation. In the
        steady-state one-shadow-at-a-time path that transaction is read-only but still
        serializes with every SQLite writer. Record the replay case exactly once, inspect
        established shadow state on a deferred WAL snapshot, and return immediately when
        a shadow is already present.

        If no shadow is visible, delegate to the base implementation. It retains the
        original writer transaction and, before inserting a candidate, performs its second
        shadow/current-component recheck, so concurrent proposal races keep the same
        mutation semantics. Cold domains also stay on the base bootstrap path.
        """
        self._record_replay_case(domain, trajectory)
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
