from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Iterable
from contextvars import ContextVar
from typing import Any, Callable, TypeVar

from .skills import AdaptiveSkillLibrary


_T = TypeVar("_T")


class BundledAdaptiveSkillLibrary(AdaptiveSkillLibrary):
    """Built-in skill fast paths without changing the base plugin contract."""

    def __init__(self, db_path):
        self._async_gate_lock = threading.RLock()
        self._async_gates: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()
        self._decision_policy_snapshot: ContextVar[
            tuple[str, dict[str, Any]] | None
        ] = ContextVar(
            f"ecomevo-skill-policy-snapshot-{id(self)}",
            default=None,
        )
        super().__init__(db_path)

    def _async_gate(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        with self._async_gate_lock:
            gate = self._async_gates.get(loop)
            if gate is None:
                gate = asyncio.Lock()
                self._async_gates[loop] = gate
            return gate

    async def _run_io(self, call: Callable[..., _T], /, *args, **kwargs) -> _T:
        """Run one skill-library SQLite operation off-loop with clear cancellation."""
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

    def _relevant_with_policy(
        self,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        limit: int = 6,
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """Read active skills and an existing evolution policy from one SQLite snapshot.

        Missing-policy bootstrap deliberately remains on the existing ``policy`` write path;
        steady-state decision reads stay read-only and avoid a second connection.
        """
        haystack = (str(query) + " " + " ".join(str(x) for x in missing)).lower()
        with self._conn() as connection:
            # A deferred read transaction pins both SELECTs to one WAL snapshot without
            # taking the SQLite writer lock. Writer-stage profiling counts deferred BEGIN
            # as a writer only if DML follows, so this stays a read-only boundary.
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM runtime_skills WHERE domain=? AND status='active' "
                "ORDER BY updated_at DESC LIMIT 100",
                (domain,),
            ).fetchall()
            policy_row = connection.execute(
                "SELECT * FROM evolution_policy WHERE domain=?",
                (domain,),
            ).fetchone()

        candidates = [self._decode(row) for row in rows]
        scored: list[tuple[float, Any]] = []
        for skill in candidates:
            term_hits = sum(
                1 for term in skill.trigger_terms
                if term and term.lower() in haystack
            )
            score = (
                0.46 * skill.posterior_mean
                + 0.34 * skill.shadow_score
                + min(0.20, 0.05 * term_hits)
            )
            scored.append((score, skill))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        selected = [skill for _, skill in scored[: max(1, int(limit))]]

        policy = None
        if policy_row is not None:
            policy = {
                "domain": domain,
                "promotion_threshold": float(policy_row["promotion_threshold"]),
                "retirement_threshold": float(policy_row["retirement_threshold"]),
                "exploration": float(policy_row["exploration"]),
                "updates": int(policy_row["updates"]),
                "updated_at": float(policy_row["updated_at"]),
            }
        return selected, policy

    def relevant(
        self,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        limit: int = 6,
    ) -> list[Any]:
        selected, policy = self._relevant_with_policy(
            domain,
            query=query,
            missing=missing,
            limit=limit,
        )
        if policy is None:
            self._decision_policy_snapshot.set(None)
        else:
            self._decision_policy_snapshot.set((str(domain), policy))
        return selected

    def policy(self, domain: str) -> dict[str, Any]:
        prepared = self._decision_policy_snapshot.get()
        if prepared is not None:
            # The fused snapshot is deliberately one-shot. A policy lookup for another
            # domain is a broken adjacency, so discard rather than leaving stale state
            # available for a later lookup in this task.
            self._decision_policy_snapshot.set(None)
            if prepared[0] == str(domain):
                return dict(prepared[1])
        return super().policy(domain)

    def _invalidate_decision_policy_snapshot(self) -> None:
        self._decision_policy_snapshot.set(None)

    def _adapt_policy(self, *args, **kwargs):
        # Synchronous evolution-policy writes invalidate a not-yet-consumed read snapshot.
        self._invalidate_decision_policy_snapshot()
        return super()._adapt_policy(*args, **kwargs)

    def record_outcome(self, *args, **kwargs):
        # ``record_outcome`` updates evolution_policy inside its own transaction rather
        # than calling ``_adapt_policy``; invalidate before entering that write boundary.
        self._invalidate_decision_policy_snapshot()
        return super().record_outcome(*args, **kwargs)

    async def note_run_async(
        self,
        domain: str,
        *,
        success: bool,
        skill_used: bool = False,
    ) -> None:
        # ContextVar updates made inside ``asyncio.to_thread`` do not propagate back to
        # the event-loop task. Clear in the caller context before offloading the write.
        self._invalidate_decision_policy_snapshot()
        await self._run_io(
            self.note_run,
            domain,
            success=success,
            skill_used=skill_used,
        )
