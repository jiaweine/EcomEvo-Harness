from __future__ import annotations

import asyncio
import json
import threading
import time
import weakref
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, TypeVar

from ecomevo.models import RuntimeEvent

from .event_store import EventStore


_APPEND_GROUP_LIMIT = 64
_T = TypeVar("_T")


@dataclass(slots=True)
class _GroupedAppend:
    session_id: str
    event_type: str
    payload: dict[str, Any]
    future: asyncio.Future[RuntimeEvent]


@dataclass(slots=True)
class _LoopAppendGroup:
    queue: list[_GroupedAppend] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class BundledEventStore(EventStore):
    """Built-in EventStore fast paths that preserve the base persistence contract.

    The base ``EventStore`` stays the compatibility surface for plugins and direct
    callers. This subclass only bundles/offloads writes whose ordering and rollback
    boundary are known by the built-in engine.
    """

    def __init__(self, path):
        self._append_group_lock = threading.RLock()
        self._append_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopAppendGroup
        ] = weakref.WeakKeyDictionary()
        super().__init__(path)

    @staticmethod
    async def _await_durable_thread(
        fn: Callable[..., _T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Run one existing durable operation off-loop without ambiguous cancellation.

        ``asyncio.to_thread`` cannot stop a function once it has started. Shield the
        worker task and, if the caller is cancelled, wait until SQLite has committed or
        rolled back before surfacing ``CancelledError``. This preserves the same
        durability-before-return boundary as the synchronous methods.
        """
        worker = asyncio.create_task(asyncio.to_thread(partial(fn, *args, **kwargs)))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError as cancelled:
            try:
                await asyncio.shield(worker)
            except Exception:
                raise
            raise cancelled

    def create_session_events_checkpoint(
        self,
        session_id: str,
        events: list[tuple[str, dict[str, Any]]],
        snapshot: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ) -> tuple[list[RuntimeEvent], dict[str, Any]]:
        """Atomically persist a new session, ordered events, and initial checkpoint."""
        if not events:
            raise ValueError("bootstrap bundle requires at least one event")

        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    session_id,
                    parent_session_id,
                    parent_seq,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False, default=str),
                ),
            )

            persisted: list[RuntimeEvent] = []
            tail: dict[str, Any] = {"seq": None, "hash": None}
            for event_type, payload in events:
                event = self._append_in_transaction(
                    c,
                    session_id,
                    str(event_type),
                    payload,
                    tail=tail,
                )
                persisted.append(event)
                tail = {"seq": event.seq, "hash": event.hash}

            reference = self._save_checkpoint_in_transaction(
                c,
                session_id,
                snapshot,
                tail=tail,
            )
            return persisted, reference

    async def create_session_events_checkpoint_async(
        self,
        session_id: str,
        events: list[tuple[str, dict[str, Any]]],
        snapshot: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ) -> tuple[list[RuntimeEvent], dict[str, Any]]:
        """Async scheduling wrapper around the exact bootstrap transaction above."""
        return await self._await_durable_thread(
            self.create_session_events_checkpoint,
            session_id,
            events,
            snapshot,
            meta=meta,
            parent_session_id=parent_session_id,
            parent_seq=parent_seq,
        )

    async def save_checkpoint_and_append_async(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        event_type: str,
        event_payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> tuple[dict[str, Any], RuntimeEvent]:
        """Off-loop wrapper preserving one-checkpoint/one-transaction semantics."""
        return await self._await_durable_thread(
            self.save_checkpoint_and_append,
            session_id,
            snapshot,
            event_type,
            event_payload,
            seq=seq,
        )

    async def append_grouped(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        """Durably coalesce sinkless appends without blocking the caller event loop.

        One loop-local worker preserves enqueue order and drains bounded batches. Each
        caller waits until its SQLite transaction has committed before returning. The
        actual SQLite work runs in a worker thread so short durable writes do not freeze
        unrelated async tool/reasoning tasks on the main loop.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[RuntimeEvent] = loop.create_future()
        request = _GroupedAppend(
            session_id=str(session_id),
            event_type=str(event_type),
            payload=payload,
            future=future,
        )
        with self._append_group_lock:
            group = self._append_groups.get(loop)
            if group is None:
                group = _LoopAppendGroup()
                self._append_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_append_group(group))

        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    @staticmethod
    def _fail_requests(requests: list[_GroupedAppend], error: BaseException) -> None:
        for request in requests:
            if not request.future.done():
                request.future.set_exception(error)

    def _stop_append_group(self, group: _LoopAppendGroup) -> list[_GroupedAppend]:
        with self._append_group_lock:
            queued = list(group.queue)
            group.queue.clear()
            group.scheduled = False
            group.worker = None
        return queued

    async def _flush_append_group(self, group: _LoopAppendGroup) -> None:
        try:
            while True:
                await asyncio.sleep(0)
                with self._append_group_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    batch = list(group.queue[:_APPEND_GROUP_LIMIT])
                    del group.queue[: len(batch)]

                persist_task = asyncio.create_task(
                    asyncio.to_thread(self._persist_append_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    persisted = await asyncio.shield(persist_task)
                except asyncio.CancelledError as cancelled:
                    worker_cancelled = cancelled
                    try:
                        persisted = await asyncio.shield(persist_task)
                    except Exception as exc:
                        self._fail_requests(batch, exc)
                        queued = self._stop_append_group(group)
                        self._fail_requests(
                            queued,
                            RuntimeError("event append worker cancelled before persistence"),
                        )
                        raise cancelled
                except Exception:
                    for index, request in enumerate(batch):
                        if request.future.done():
                            continue
                        single_task = asyncio.create_task(
                            asyncio.to_thread(
                                self.append,
                                request.session_id,
                                request.event_type,
                                request.payload,
                            )
                        )
                        try:
                            event = await asyncio.shield(single_task)
                        except asyncio.CancelledError as cancelled:
                            try:
                                event = await asyncio.shield(single_task)
                            except Exception as exc:
                                request.future.set_exception(exc)
                            else:
                                request.future.set_result(event)
                            pending = batch[index + 1 :]
                            self._fail_requests(
                                pending,
                                RuntimeError(
                                    "event append worker cancelled before isolated persistence"
                                ),
                            )
                            queued = self._stop_append_group(group)
                            self._fail_requests(
                                queued,
                                RuntimeError("event append worker cancelled before persistence"),
                            )
                            raise cancelled
                        except Exception as exc:
                            request.future.set_exception(exc)
                        else:
                            request.future.set_result(event)
                    continue

                for request, event in zip(batch, persisted):
                    if not request.future.done():
                        request.future.set_result(event)

                if worker_cancelled is not None:
                    queued = self._stop_append_group(group)
                    self._fail_requests(
                        queued,
                        RuntimeError("event append worker cancelled before persistence"),
                    )
                    raise worker_cancelled
        except asyncio.CancelledError:
            queued = self._stop_append_group(group)
            self._fail_requests(
                queued,
                RuntimeError("event append worker cancelled before persistence"),
            )
            raise

    def _persist_append_group(self, batch: list[_GroupedAppend]) -> list[RuntimeEvent]:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            tails: dict[str, dict[str, Any] | Any] = {}
            persisted: list[RuntimeEvent] = []
            for request in batch:
                if request.session_id not in tails:
                    tail = self._session_tail(c, request.session_id)
                    if tail is None:
                        raise KeyError(f"unknown session: {request.session_id}")
                    tails[request.session_id] = tail
                event = self._append_in_transaction(
                    c,
                    request.session_id,
                    request.event_type,
                    request.payload,
                    tail=tails[request.session_id],
                )
                persisted.append(event)
                tails[request.session_id] = {"seq": event.seq, "hash": event.hash}
            return persisted
