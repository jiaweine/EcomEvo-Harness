from __future__ import annotations

import asyncio
import json
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

from ecomevo.models import RuntimeEvent

from .event_store import EventStore


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


class BundledEventStore(EventStore):
    """Built-in EventStore fast paths that preserve the base persistence contract.

    The base ``EventStore`` stays the compatibility surface for plugins and direct
    callers. This subclass only bundles writes whose ordering and rollback boundary
    are known by the built-in engine.
    """

    def __init__(self, path):
        self._append_group_lock = threading.RLock()
        self._append_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopAppendGroup
        ] = weakref.WeakKeyDictionary()
        super().__init__(path)

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
        """Atomically persist a new session, ordered events, and initial checkpoint.

        The checkpoint is bound to the final event in ``events``. Any serialization
        or SQLite failure rolls back the entire bootstrap, so a run can never become
        visible with only a prefix of its initial durable state.
        """
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

    async def append_grouped(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        """Coalesce sinkless append calls that arrive in the same event-loop turn.

        Every caller waits for the shared SQLite transaction to commit before this
        coroutine returns. The queue is loop-local so one EventStore can be used by
        independent asyncio loops without sharing Futures across loops.
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
                loop.create_task(self._flush_append_group(loop, group))

        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            # Preserve the synchronous append contract: once an append has entered the
            # persistence queue, cancellation is observed only after durability (or a
            # persistence error) is known. This avoids ambiguous late commits.
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    async def _flush_append_group(
        self,
        loop: asyncio.AbstractEventLoop,
        group: _LoopAppendGroup,
    ) -> None:
        # One scheduler turn is enough for peer runtime tasks to enqueue the same
        # lifecycle event. No fixed sleep/latency budget is introduced.
        await asyncio.sleep(0)
        with self._append_group_lock:
            batch = list(group.queue)
            group.queue.clear()
            group.scheduled = False
        if not batch:
            return

        try:
            persisted = self._persist_append_group(batch)
        except Exception:
            # One malformed payload/session must not poison unrelated sessions. The
            # shared transaction has rolled back, so isolate by replaying each request
            # through the proven single-append path.
            for request in batch:
                if request.future.done():
                    continue
                try:
                    event = self.append(
                        request.session_id,
                        request.event_type,
                        request.payload,
                    )
                except Exception as exc:
                    request.future.set_exception(exc)
                else:
                    request.future.set_result(event)
            return

        for request, event in zip(batch, persisted):
            if not request.future.done():
                request.future.set_result(event)

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
