from __future__ import annotations

import asyncio
import threading
import time
import weakref
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from .skills import AdaptiveSkillLibrary


_NOTE_RUN_GROUP_LIMIT = 64
_T = TypeVar("_T")


@dataclass(slots=True)
class _QueuedNoteRun:
    domain: str
    success: bool
    skill_used: bool
    future: asyncio.Future[None]
    started: bool = False

    @property
    def metadata_only(self) -> bool:
        # In AdaptiveSkillLibrary._adapt_policy_in_transaction this exact case leaves
        # promotion / retirement / exploration unchanged and advances only updates/time.
        return self.success and not self.skill_used


@dataclass(slots=True)
class _LoopNoteRunGroup:
    queue: list[_QueuedNoteRun] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class BundledAdaptiveSkillLibrary(AdaptiveSkillLibrary):
    """Built-in skill fast paths without changing the base plugin contract."""

    def __init__(self, db_path):
        self._async_gate_lock = threading.RLock()
        self._async_gates: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()
        self._note_run_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopNoteRunGroup
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

    @staticmethod
    def _policy_payload(domain: str, policy_row: Any | None) -> dict[str, Any] | None:
        if policy_row is None:
            return None
        return {
            "domain": domain,
            "promotion_threshold": float(policy_row["promotion_threshold"]),
            "retirement_threshold": float(policy_row["retirement_threshold"]),
            "exploration": float(policy_row["exploration"]),
            "updates": int(policy_row["updates"]),
            "updated_at": float(policy_row["updated_at"]),
        }

    def _relevant_with_policy_from_connection(
        self,
        connection,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        limit: int = 6,
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """Read the skill/policy portion of a caller-owned read snapshot."""
        haystack = (str(query) + " " + " ".join(str(x) for x in missing)).lower()
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
        return selected, self._policy_payload(domain, policy_row)

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
        with self._conn() as connection:
            # A deferred read transaction pins both SELECTs to one WAL snapshot without
            # taking the writer lock. Writer-stage profiling counts deferred BEGIN
            # as a writer only if DML follows, so this stays a read-only boundary.
            connection.execute("BEGIN")
            return self._relevant_with_policy_from_connection(
                connection,
                domain,
                query=query,
                missing=missing,
                limit=limit,
            )

    def prepare_decision_snapshot(
        self,
        routing: Any,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        tools: Iterable[str] = (),
        limit: int = 6,
    ) -> dict[str, Any] | None:
        """Fuse built-in skill and routing reads into one short coherent snapshot.

        The connection and deferred transaction are closed before this method returns;
        callers may safely await the model afterwards without pinning SQLite state. Custom
        routing implementations simply do not expose the optional connection-aware hook.
        """
        prepare_routing = getattr(routing, "prepare_context_from_connection", None)
        if not callable(prepare_routing) or str(getattr(routing, "path", "")) != str(self.path):
            return None

        # A normal initial ``relevant`` call can leave #53's one-shot policy snapshot in
        # this task. A fused round owns its policy explicitly, so discard any older copy.
        self._invalidate_decision_policy_snapshot()
        with self._conn() as connection:
            connection.execute("BEGIN")
            selected, policy = self._relevant_with_policy_from_connection(
                connection,
                domain,
                query=query,
                missing=missing,
                limit=limit,
            )
            if policy is None:
                # Preserve cold-start bootstrap semantics: close the read snapshot and let
                # the existing policy() path perform its guarded write before retrying.
                return None
            exploration = max(0.0, min(1.0, float(policy.get("exploration", 0.6))))
            prepared = prepare_routing(
                connection,
                domain,
                tools=[str(tool) for tool in tools if str(tool)],
                exploration=exploration,
            )
        return {
            "skills": selected,
            "policy": policy,
            "routing": prepared,
            "exploration": exploration,
        }

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

    @staticmethod
    def _fail_note_run_requests(
        requests: list[_QueuedNoteRun],
        error: BaseException,
    ) -> None:
        for request in requests:
            if not request.future.done():
                request.future.set_exception(error)

    def _stop_note_run_group(self, group: _LoopNoteRunGroup) -> list[_QueuedNoteRun]:
        with self._async_gate_lock:
            queued = list(group.queue)
            group.queue.clear()
            group.scheduled = False
            group.worker = None
        return queued

    async def _record_note_run_grouped(
        self,
        domain: str,
        *,
        success: bool,
        skill_used: bool,
    ) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        request = _QueuedNoteRun(
            domain=str(domain),
            success=bool(success),
            skill_used=bool(skill_used),
            future=future,
        )
        with self._async_gate_lock:
            group = self._note_run_groups.get(loop)
            if group is None:
                group = _LoopNoteRunGroup()
                self._note_run_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_note_run_group(group))

        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            removed = False
            with self._async_gate_lock:
                if not request.started and not future.done():
                    try:
                        group.queue.remove(request)
                    except ValueError:
                        pass
                    else:
                        future.cancel()
                        removed = True
            if removed:
                raise cancelled
            # Once dequeued for persistence, preserve #51's durability-before-cancel rule.
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    async def _flush_note_run_group(self, group: _LoopNoteRunGroup) -> None:
        try:
            while True:
                # One scheduler turn admits naturally phase-aligned finalizers without
                # imposing a fixed batching window on isolated runs.
                await asyncio.sleep(0)
                with self._async_gate_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return

                    first = group.queue[0]
                    if first.metadata_only:
                        batch: list[_QueuedNoteRun] = []
                        while (
                            group.queue
                            and len(batch) < _NOTE_RUN_GROUP_LIMIT
                            and group.queue[0].metadata_only
                        ):
                            request = group.queue.pop(0)
                            request.started = True
                            batch.append(request)
                    else:
                        request = group.queue.pop(0)
                        request.started = True
                        batch = [request]

                persist_task = asyncio.create_task(
                    self._run_io(self._persist_note_run_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    await asyncio.shield(persist_task)
                except asyncio.CancelledError as cancelled:
                    worker_cancelled = cancelled
                    try:
                        await asyncio.shield(persist_task)
                    except Exception as exc:
                        self._fail_note_run_requests(batch, exc)
                        queued = self._stop_note_run_group(group)
                        self._fail_note_run_requests(
                            queued,
                            RuntimeError("skill note-run worker cancelled before persistence"),
                        )
                        raise cancelled
                except Exception:
                    # Shared metadata-only batches roll back as a unit. Retry one request
                    # at a time so an isolated SQLite failure cannot poison valid peers.
                    for index, request in enumerate(batch):
                        if request.future.done():
                            continue
                        single_task = asyncio.create_task(
                            self._run_io(self._persist_note_run_group, [request])
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
                            self._fail_note_run_requests(
                                batch[index + 1 :],
                                RuntimeError(
                                    "skill note-run worker cancelled before isolated persistence"
                                ),
                            )
                            queued = self._stop_note_run_group(group)
                            self._fail_note_run_requests(
                                queued,
                                RuntimeError("skill note-run worker cancelled before persistence"),
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
                    queued = self._stop_note_run_group(group)
                    self._fail_note_run_requests(
                        queued,
                        RuntimeError("skill note-run worker cancelled before persistence"),
                    )
                    raise worker_cancelled
        except asyncio.CancelledError:
            queued = self._stop_note_run_group(group)
            self._fail_note_run_requests(
                queued,
                RuntimeError("skill note-run worker cancelled before persistence"),
            )
            raise

    def _persist_note_run_group(self, batch: list[_QueuedNoteRun]) -> None:
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if all(request.metadata_only for request in batch):
                # This path deliberately batches only the adaptation no-op case. Preserve
                # the exact durable update count while leaving all strategy parameters
                # untouched. Distinct domains can safely share the same SQLite transaction.
                counts: dict[str, int] = {}
                for request in batch:
                    counts[request.domain] = counts.get(request.domain, 0) + 1
                for domain, count in counts.items():
                    now = time.time()
                    self._policy_in_transaction(connection, domain, now=now)
                    connection.execute(
                        "UPDATE evolution_policy SET updates=updates+?,updated_at=? WHERE domain=?",
                        (count, now, domain),
                    )
                return

            # Learning-bearing requests are barriers and therefore always reach here alone.
            if len(batch) != 1:  # pragma: no cover - guarded by queue partitioning
                raise RuntimeError("learning-bearing skill note runs must not be grouped")
            request = batch[0]
            self._adapt_policy_in_transaction(
                connection,
                request.domain,
                success=request.success,
                skill_used=request.skill_used,
                now=time.time(),
            )

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

        # Preserve the established extension behavior for subclasses that override the
        # synchronous method: the built-in batching fast path must not bypass custom logic.
        if type(self).note_run is not AdaptiveSkillLibrary.note_run:
            await self._run_io(
                self.note_run,
                domain,
                success=success,
                skill_used=skill_used,
            )
            return

        await self._record_note_run_grouped(
            domain,
            success=success,
            skill_used=skill_used,
        )
