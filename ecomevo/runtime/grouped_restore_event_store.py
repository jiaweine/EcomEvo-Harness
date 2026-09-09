from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import weakref
from dataclasses import dataclass, field
from typing import Any

from .bundled_event_store import BundledEventStore
from .event_store import EventStore


_RESTORE_GROUP_LIMIT = 64


@dataclass(slots=True)
class _GroupedRestore:
    session_id: str
    seq: int
    future: asyncio.Future[dict[str, Any] | None]


@dataclass(slots=True)
class _LoopRestoreGroup:
    queue: list[_GroupedRestore] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class GroupedRestoreBundledEventStore(BundledEventStore):
    """Built-in EventStore with bounded exact-checkpoint read coalescing.

    Public/synchronous ``EventStore.restore_checkpoint`` remains the compatibility
    contract. The optional async method is used only by the built-in sinkless Engine
    recovery path when it already owns an exact durable checkpoint sequence.
    """

    def __init__(self, path):
        self._restore_group_lock = threading.RLock()
        self._restore_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopRestoreGroup
        ] = weakref.WeakKeyDictionary()
        super().__init__(path)

    @staticmethod
    def _restore_from_connection(connection, session_id: str, seq: int):
        """Mirror EventStore.restore_checkpoint using a caller-owned read connection."""
        row = connection.execute(
            "SELECT seq,snapshot_blob,state_hash,event_hash FROM snapshots "
            "WHERE session_id=? AND seq<=? ORDER BY seq DESC LIMIT 1",
            (session_id, int(seq)),
        ).fetchone()
        if not row:
            return None

        checkpoint_seq = int(row["seq"])
        event_hash = "GENESIS"
        if checkpoint_seq:
            event = connection.execute(
                "SELECT hash FROM events WHERE session_id=? AND seq=?",
                (session_id, checkpoint_seq),
            ).fetchone()
            if event is None:
                return None
            event_hash = str(event["hash"])

        blob = str(row["snapshot_blob"])
        if not blob.startswith("json:"):
            return None
        state = json.loads(blob[5:])
        body = EventStore._state_body(state)
        expected_state = str(
            row["state_hash"] or hashlib.sha256(body.encode()).hexdigest()
        )
        expected_event = str(row["event_hash"] or event_hash)
        if (
            hashlib.sha256(body.encode()).hexdigest() != expected_state
            or event_hash != expected_event
        ):
            return None
        return {
            **state,
            "_checkpoint": {
                "session_id": session_id,
                "seq": checkpoint_seq,
                "state_hash": expected_state,
                "event_hash": expected_event,
            },
        }

    async def restore_checkpoint_async(
        self,
        session_id: str,
        seq: int,
    ) -> dict[str, Any] | None:
        """Restore an exact durable checkpoint without blocking the event loop."""
        # Preserve explicit synchronous overrides instead of silently bypassing plugin or
        # profiling semantics through the grouped built-in helper.
        if type(self).restore_checkpoint is not EventStore.restore_checkpoint:
            return await asyncio.to_thread(self.restore_checkpoint, session_id, int(seq))
        return await self.restore_checkpoint_grouped(session_id, int(seq))

    async def restore_checkpoint_grouped(
        self,
        session_id: str,
        seq: int,
    ) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        request = _GroupedRestore(str(session_id), int(seq), future)
        with self._restore_group_lock:
            group = self._restore_groups.get(loop)
            if group is None:
                group = _LoopRestoreGroup()
                self._restore_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_restore_group(group))

        return await future

    def _stop_restore_group(self, group: _LoopRestoreGroup) -> list[_GroupedRestore]:
        with self._restore_group_lock:
            queued = list(group.queue)
            group.queue.clear()
            group.scheduled = False
            group.worker = None
        return queued

    @staticmethod
    def _fail_restore_requests(batch: list[_GroupedRestore], exc: Exception) -> None:
        for request in batch:
            if not request.future.done():
                request.future.set_exception(exc)

    async def _flush_restore_group(self, group: _LoopRestoreGroup) -> None:
        try:
            while True:
                # One scheduler turn lets peer recovery tasks join the bounded read batch.
                await asyncio.sleep(0)
                with self._restore_group_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    candidates = list(group.queue[:_RESTORE_GROUP_LIMIT])
                    del group.queue[: len(candidates)]
                    batch = [
                        request for request in candidates if not request.future.done()
                    ]
                if not batch:
                    continue

                read_task = asyncio.create_task(
                    asyncio.to_thread(self._restore_checkpoint_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    values = await asyncio.shield(read_task)
                except asyncio.CancelledError as cancelled:
                    worker_cancelled = cancelled
                    try:
                        values = await asyncio.shield(read_task)
                    except Exception as exc:
                        self._fail_restore_requests(batch, exc)
                        queued = self._stop_restore_group(group)
                        self._fail_restore_requests(
                            queued,
                            RuntimeError("restore group worker cancelled before read"),
                        )
                        raise cancelled
                except Exception:
                    # Read failures have no durability ambiguity. Isolate requests through
                    # the unchanged synchronous compatibility method so one malformed
                    # checkpoint cannot poison unrelated peers in the shared batch.
                    for request in batch:
                        if request.future.done():
                            continue
                        try:
                            value = await asyncio.to_thread(
                                self.restore_checkpoint,
                                request.session_id,
                                request.seq,
                            )
                        except Exception as exc:
                            request.future.set_exception(exc)
                        else:
                            request.future.set_result(value)
                    continue

                for request, value in zip(batch, values):
                    if not request.future.done():
                        request.future.set_result(value)

                if worker_cancelled is not None:
                    queued = self._stop_restore_group(group)
                    self._fail_restore_requests(
                        queued,
                        RuntimeError("restore group worker cancelled before read"),
                    )
                    raise worker_cancelled
        except asyncio.CancelledError:
            queued = self._stop_restore_group(group)
            self._fail_restore_requests(
                queued,
                RuntimeError("restore group worker cancelled before read"),
            )
            raise

    def _restore_checkpoint_group(
        self,
        batch: list[_GroupedRestore],
    ) -> list[dict[str, Any] | None]:
        # Unlike EventStore writes, exact checkpoint reads do not take the writer lock.
        # One connection per bounded batch removes per-request connection/thread setup
        # while WAL still allows independent writers to make progress.
        with self._conn() as connection:
            return [
                self._restore_from_connection(
                    connection,
                    request.session_id,
                    request.seq,
                )
                for request in batch
            ]