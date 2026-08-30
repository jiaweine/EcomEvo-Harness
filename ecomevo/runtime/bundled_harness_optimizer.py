from __future__ import annotations

import asyncio
import threading
import weakref
from typing import Any, Callable, TypeVar

from .harness_optimizer import HarnessEvolutionOptimizer


_T = TypeVar("_T")


class BundledHarnessEvolutionOptimizer(HarnessEvolutionOptimizer):
    """Built-in Harness optimizer fast paths that preserve the base plugin contract.

    The public ``HarnessEvolutionOptimizer`` remains the compatibility surface. This
    subclass only adds optional methods used by the built-in Engine to keep phase-aligned
    SQLite work off the asyncio thread and to avoid decoding a full optimizer snapshot
    when the runtime needs only active/shadow generations.
    """

    def __init__(self, db_path, *, sandbox=None):
        self._async_gate_lock = threading.RLock()
        self._async_gates: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()
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
