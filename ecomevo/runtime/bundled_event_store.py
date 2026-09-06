from __future__ import annotations

import asyncio
import json
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from ecomevo.models import EvolutionPatch, RuntimeEvent

from .event_store import EventStore


_APPEND_GROUP_LIMIT = 64
_BOOTSTRAP_GROUP_LIMIT = 64
_CHECKPOINT_GROUP_LIMIT = 64
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


@dataclass(slots=True)
class _GroupedBootstrap:
    session_id: str
    events: list[tuple[str, dict[str, Any]]]
    snapshot: dict[str, Any]
    meta: dict[str, Any] | None
    parent_session_id: str | None
    parent_seq: int | None
    future: asyncio.Future[tuple[list[RuntimeEvent], dict[str, Any]]]


@dataclass(slots=True)
class _LoopBootstrapGroup:
    queue: list[_GroupedBootstrap] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _GroupedCheckpoint:
    session_id: str
    snapshot: dict[str, Any]
    event_type: str
    event_payload: dict[str, Any]
    seq: int | None
    future: asyncio.Future[tuple[dict[str, Any], RuntimeEvent]]


@dataclass(slots=True)
class _LoopCheckpointGroup:
    queue: list[_GroupedCheckpoint] = field(default_factory=list)
    scheduled: bool = False
    worker: asyncio.Task[None] | None = None


class BundledEventStore(EventStore):
    """Built-in EventStore fast paths that preserve the base persistence contract.

    The base ``EventStore`` stays the compatibility surface for plugins and direct
    callers. This subclass only bundles or offloads writes whose ordering and rollback
    boundary are already known by the built-in engine.
    """

    def __init__(self, path):
        self._append_group_lock = threading.RLock()
        self._append_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopAppendGroup
        ] = weakref.WeakKeyDictionary()
        self._bootstrap_group_lock = threading.RLock()
        self._bootstrap_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopBootstrapGroup
        ] = weakref.WeakKeyDictionary()
        self._checkpoint_group_lock = threading.RLock()
        self._checkpoint_groups: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopCheckpointGroup
        ] = weakref.WeakKeyDictionary()
        # SQLite writes are already serialized by EventStore._lock. Queueing before
        # entering the executor prevents a burst of coroutines from occupying the
        # default thread pool with workers that can do nothing except wait on that lock.
        self._io_gates: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Lock
        ] = weakref.WeakKeyDictionary()
        super().__init__(path)

    def _io_gate(self, loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
        with self._append_group_lock:
            gate = self._io_gates.get(loop)
            if gate is None:
                gate = asyncio.Lock()
                self._io_gates[loop] = gate
            return gate

    async def _run_io(self, call: Callable[..., _T], /, *args, **kwargs) -> _T:
        """Run one built-in EventStore operation off-loop without changing durability.

        Cancellation while waiting for the async gate is safe to propagate immediately:
        no SQLite operation has started. Once submitted to a worker, cancellation is
        delayed until commit/rollback is known so callers never observe an ambiguous
        late durable write.
        """
        loop = asyncio.get_running_loop()
        gate = self._io_gate(loop)
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
        # A subclass may deliberately instrument or alter the synchronous bootstrap
        # boundary. Preserve that observable compatibility contract instead of silently
        # bypassing the override through the built-in group-commit worker.
        if (
            type(self).create_session_events_checkpoint
            is not BundledEventStore.create_session_events_checkpoint
        ):
            return await self._run_io(
                self.create_session_events_checkpoint,
                session_id,
                events,
                snapshot,
                meta=meta,
                parent_session_id=parent_session_id,
                parent_seq=parent_seq,
            )
        return await self.create_session_events_checkpoint_grouped(
            session_id,
            events,
            snapshot,
            meta=meta,
            parent_session_id=parent_session_id,
            parent_seq=parent_seq,
        )

    async def create_session_events_checkpoint_grouped(
        self,
        session_id: str,
        events: list[tuple[str, dict[str, Any]]],
        snapshot: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        parent_seq: int | None = None,
    ) -> tuple[list[RuntimeEvent], dict[str, Any]]:
        """Coalesce independent sinkless session bootstraps into durable transactions."""
        if not events:
            raise ValueError("bootstrap bundle requires at least one event")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[list[RuntimeEvent], dict[str, Any]]] = (
            loop.create_future()
        )
        request = _GroupedBootstrap(
            session_id=str(session_id),
            events=list(events),
            snapshot=snapshot,
            meta=dict(meta) if meta is not None else None,
            parent_session_id=parent_session_id,
            parent_seq=parent_seq,
            future=future,
        )
        with self._bootstrap_group_lock:
            group = self._bootstrap_groups.get(loop)
            if group is None:
                group = _LoopBootstrapGroup()
                self._bootstrap_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_bootstrap_group(group))

        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            # Once queued, retain the synchronous durability contract. Do not expose
            # cancellation until this session is known to be committed or rolled back.
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    async def save_checkpoint_and_append_async(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        event_type: str,
        event_payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> tuple[dict[str, Any], RuntimeEvent]:
        return await self.save_checkpoint_and_append_grouped(
            session_id,
            snapshot,
            event_type,
            event_payload,
            seq=seq,
        )

    async def save_checkpoint_and_append_grouped(
        self,
        session_id: str,
        snapshot: dict[str, Any],
        event_type: str,
        event_payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> tuple[dict[str, Any], RuntimeEvent]:
        """Durably coalesce sinkless checkpoint+audit writes across active sessions.

        Every checkpoint remains bound to the session tail immediately before its audit
        event. Callers only return after the shared transaction has committed. The
        queue is loop-local and bounded, and all SQLite work shares the normal I/O gate.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[dict[str, Any], RuntimeEvent]] = loop.create_future()
        request = _GroupedCheckpoint(
            session_id=str(session_id),
            snapshot=snapshot,
            event_type=str(event_type),
            event_payload=dict(event_payload or {}),
            seq=seq,
            future=future,
        )
        with self._checkpoint_group_lock:
            group = self._checkpoint_groups.get(loop)
            if group is None:
                group = _LoopCheckpointGroup()
                self._checkpoint_groups[loop] = group
            group.queue.append(request)
            if not group.scheduled:
                group.scheduled = True
                group.worker = loop.create_task(self._flush_checkpoint_group(group))

        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError as cancelled:
            # Once queued, preserve the synchronous durability contract: cancellation is
            # observable only after checkpoint+audit persistence has committed or failed.
            try:
                await asyncio.shield(future)
            except Exception:
                raise
            raise cancelled

    async def verify_chain_async(self, session_id: str) -> bool:
        return await self._run_io(self.verify_chain, session_id)

    @staticmethod
    def _existing_patch_payload(row, patch: EvolutionPatch) -> dict[str, Any]:
        try:
            return json.loads(row["payload_json"])
        except Exception:
            return {
                "patch_id": "existing",
                "accepted": patch.accepted,
                "target": patch.target,
                "patch": patch.patch,
            }

    def save_patch_if_novel(self, patch: EvolutionPatch) -> dict[str, Any] | None:
        """Return duplicate patches without reserving SQLite's writer slot.

        ``evolution_patches`` is append-only through the runtime contract and fingerprint
        uniqueness is enforced by SQLite. A read hit is therefore safe to return without
        ``BEGIN IMMEDIATE``. A miss rechecks under the original writer lock before INSERT
        so concurrent first observations retain the base EventStore novelty semantics.
        """
        fingerprint = self._patch_fingerprint(patch)
        with self._conn() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fingerprint,),
            ).fetchone()
        if existing is not None:
            return self._existing_patch_payload(existing, patch)

        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM evolution_patches WHERE fingerprint=? LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                return self._existing_patch_payload(existing, patch)
            connection.execute(
                "INSERT INTO evolution_patches(patch_id,created_at,payload_json,fingerprint) "
                "VALUES(?,?,?,?)",
                (
                    patch.patch_id,
                    patch.created_at,
                    patch.model_dump_json(),
                    fingerprint,
                ),
            )
        return None

    async def append_grouped(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RuntimeEvent:
        """Durably coalesce sinkless appends without blocking the caller event loop.

        One loop-local worker preserves enqueue order and drains bounded batches. Each
        caller waits until its SQLite transaction has committed before returning. The
        actual SQLite work uses the same loop-local I/O gate as bootstrap/checkpoints,
        so executor workers never pile up behind EventStore._lock.
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
            # Once queued, keep the synchronous durability contract: caller cancellation
            # becomes observable only after this append has committed or failed.
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

    @staticmethod
    def _fail_bootstrap_requests(
        requests: list[_GroupedBootstrap], error: BaseException
    ) -> None:
        for request in requests:
            if not request.future.done():
                request.future.set_exception(error)

    @staticmethod
    def _fail_checkpoint_requests(
        requests: list[_GroupedCheckpoint], error: BaseException
    ) -> None:
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

    def _stop_bootstrap_group(
        self, group: _LoopBootstrapGroup
    ) -> list[_GroupedBootstrap]:
        with self._bootstrap_group_lock:
            queued = list(group.queue)
            group.queue.clear()
            group.scheduled = False
            group.worker = None
        return queued

    def _stop_checkpoint_group(
        self, group: _LoopCheckpointGroup
    ) -> list[_GroupedCheckpoint]:
        with self._checkpoint_group_lock:
            queued = list(group.queue)
            group.queue.clear()
            group.scheduled = False
            group.worker = None
        return queued

    async def _flush_append_group(self, group: _LoopAppendGroup) -> None:
        try:
            while True:
                # Give peer tasks one turn to join the next bounded batch. There is no
                # fixed sleep window, so isolated writes do not gain artificial latency.
                await asyncio.sleep(0)
                with self._append_group_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    batch = list(group.queue[:_APPEND_GROUP_LIMIT])
                    del group.queue[: len(batch)]

                persist_task = asyncio.create_task(
                    self._run_io(self._persist_append_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    persisted = await asyncio.shield(persist_task)
                except asyncio.CancelledError as cancelled:
                    # The persistence task is shielded. Even when it is only waiting for
                    # the shared async I/O gate, resolve whether this queued batch became
                    # durable before propagating worker cancellation.
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
                    # The shared transaction rolled back. Isolate malformed sessions or
                    # payloads one by one, through the same gate, so unrelated requests
                    # complete without occupying idle executor workers.
                    for index, request in enumerate(batch):
                        if request.future.done():
                            continue
                        single_task = asyncio.create_task(
                            self._run_io(
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
            # Cancellation between batches means every item still in the queue is known
            # to be non-durable. Complete those Futures rather than leaking pending waits.
            queued = self._stop_append_group(group)
            self._fail_requests(
                queued,
                RuntimeError("event append worker cancelled before persistence"),
            )
            raise

    async def _flush_bootstrap_group(self, group: _LoopBootstrapGroup) -> None:
        try:
            while True:
                await asyncio.sleep(0)
                with self._bootstrap_group_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    batch = list(group.queue[:_BOOTSTRAP_GROUP_LIMIT])
                    del group.queue[: len(batch)]

                persist_task = asyncio.create_task(
                    self._run_io(self._persist_bootstrap_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    persisted = await asyncio.shield(persist_task)
                except asyncio.CancelledError as cancelled:
                    worker_cancelled = cancelled
                    try:
                        persisted = await asyncio.shield(persist_task)
                    except Exception as exc:
                        self._fail_bootstrap_requests(batch, exc)
                        queued = self._stop_bootstrap_group(group)
                        self._fail_bootstrap_requests(
                            queued,
                            RuntimeError(
                                "bootstrap group worker cancelled before persistence"
                            ),
                        )
                        raise cancelled
                except Exception:
                    # Roll the failed shared transaction back, then isolate requests
                    # through the original atomic bootstrap operation so one duplicate or
                    # malformed session cannot poison unrelated peers.
                    for index, request in enumerate(batch):
                        if request.future.done():
                            continue
                        single_task = asyncio.create_task(
                            self._run_io(
                                self.create_session_events_checkpoint,
                                request.session_id,
                                request.events,
                                request.snapshot,
                                meta=request.meta,
                                parent_session_id=request.parent_session_id,
                                parent_seq=request.parent_seq,
                            )
                        )
                        try:
                            result = await asyncio.shield(single_task)
                        except asyncio.CancelledError as cancelled:
                            try:
                                result = await asyncio.shield(single_task)
                            except Exception as exc:
                                request.future.set_exception(exc)
                            else:
                                request.future.set_result(result)
                            pending = batch[index + 1 :]
                            self._fail_bootstrap_requests(
                                pending,
                                RuntimeError(
                                    "bootstrap group worker cancelled before isolated persistence"
                                ),
                            )
                            queued = self._stop_bootstrap_group(group)
                            self._fail_bootstrap_requests(
                                queued,
                                RuntimeError(
                                    "bootstrap group worker cancelled before persistence"
                                ),
                            )
                            raise cancelled
                        except Exception as exc:
                            request.future.set_exception(exc)
                        else:
                            request.future.set_result(result)
                    continue

                for request, result in zip(batch, persisted):
                    if not request.future.done():
                        request.future.set_result(result)

                if worker_cancelled is not None:
                    queued = self._stop_bootstrap_group(group)
                    self._fail_bootstrap_requests(
                        queued,
                        RuntimeError("bootstrap group worker cancelled before persistence"),
                    )
                    raise worker_cancelled
        except asyncio.CancelledError:
            queued = self._stop_bootstrap_group(group)
            self._fail_bootstrap_requests(
                queued,
                RuntimeError("bootstrap group worker cancelled before persistence"),
            )
            raise

    async def _flush_checkpoint_group(self, group: _LoopCheckpointGroup) -> None:
        try:
            while True:
                await asyncio.sleep(0)
                with self._checkpoint_group_lock:
                    if not group.queue:
                        group.scheduled = False
                        group.worker = None
                        return
                    batch = list(group.queue[:_CHECKPOINT_GROUP_LIMIT])
                    del group.queue[: len(batch)]

                persist_task = asyncio.create_task(
                    self._run_io(self._persist_checkpoint_group, batch)
                )
                worker_cancelled: asyncio.CancelledError | None = None
                try:
                    persisted = await asyncio.shield(persist_task)
                except asyncio.CancelledError as cancelled:
                    worker_cancelled = cancelled
                    try:
                        persisted = await asyncio.shield(persist_task)
                    except Exception as exc:
                        self._fail_checkpoint_requests(batch, exc)
                        queued = self._stop_checkpoint_group(group)
                        self._fail_checkpoint_requests(
                            queued,
                            RuntimeError(
                                "checkpoint group worker cancelled before persistence"
                            ),
                        )
                        raise cancelled
                except Exception:
                    # The shared transaction rolled back. Retry each request through the
                    # existing atomic checkpoint+audit operation so one bad session or
                    # snapshot cannot poison valid peers.
                    for index, request in enumerate(batch):
                        if request.future.done():
                            continue
                        single_task = asyncio.create_task(
                            self._run_io(
                                self.save_checkpoint_and_append,
                                request.session_id,
                                request.snapshot,
                                request.event_type,
                                request.event_payload,
                                seq=request.seq,
                            )
                        )
                        try:
                            result = await asyncio.shield(single_task)
                        except asyncio.CancelledError as cancelled:
                            try:
                                result = await asyncio.shield(single_task)
                            except Exception as exc:
                                request.future.set_exception(exc)
                            else:
                                request.future.set_result(result)
                            pending = batch[index + 1 :]
                            self._fail_checkpoint_requests(
                                pending,
                                RuntimeError(
                                    "checkpoint group worker cancelled before isolated persistence"
                                ),
                            )
                            queued = self._stop_checkpoint_group(group)
                            self._fail_checkpoint_requests(
                                queued,
                                RuntimeError(
                                    "checkpoint group worker cancelled before persistence"
                                ),
                            )
                            raise cancelled
                        except Exception as exc:
                            request.future.set_exception(exc)
                        else:
                            request.future.set_result(result)
                    continue

                for request, result in zip(batch, persisted):
                    if not request.future.done():
                        request.future.set_result(result)

                if worker_cancelled is not None:
                    queued = self._stop_checkpoint_group(group)
                    self._fail_checkpoint_requests(
                        queued,
                        RuntimeError("checkpoint group worker cancelled before persistence"),
                    )
                    raise worker_cancelled
        except asyncio.CancelledError:
            queued = self._stop_checkpoint_group(group)
            self._fail_checkpoint_requests(
                queued,
                RuntimeError("checkpoint group worker cancelled before persistence"),
            )
            raise

    @staticmethod
    def _session_tails_for_append_group(
        c,
        session_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Load starting tails for an already-formed append group set-wise.

        The async group worker is bounded to ``_APPEND_GROUP_LIMIT`` requests,
        so production batches use one query. Chunking also preserves the private
        helper's behavior for direct oversized callers without exceeding the
        bounded placeholder count in any SQL statement.
        """
        unique_ids = list(dict.fromkeys(str(session_id) for session_id in session_ids))
        tails: dict[str, dict[str, Any]] = {}
        for start in range(0, len(unique_ids), _APPEND_GROUP_LIMIT):
            chunk = unique_ids[start : start + _APPEND_GROUP_LIMIT]
            if not chunk:
                continue
            values = ",".join("(?)" for _ in chunk)
            rows = c.execute(  # nosec B608 - SQL shape is bounded local placeholders only
                f"""WITH wanted(session_id) AS (VALUES {values}),
                    latest AS (
                        SELECT e.session_id,MAX(e.seq) AS seq
                        FROM events AS e
                        JOIN wanted AS w ON w.session_id=e.session_id
                        GROUP BY e.session_id
                    )
                    SELECT s.session_id,latest.seq,e.hash
                    FROM wanted AS w
                    JOIN sessions AS s ON s.session_id=w.session_id
                    LEFT JOIN latest ON latest.session_id=s.session_id
                    LEFT JOIN events AS e
                      ON e.session_id=latest.session_id AND e.seq=latest.seq""",
                chunk,
            ).fetchall()
            tails.update(
                {
                    str(row["session_id"]): {"seq": row["seq"], "hash": row["hash"]}
                    for row in rows
                }
            )
        return tails

    def _persist_append_group(self, batch: list[_GroupedAppend]) -> list[RuntimeEvent]:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            ordered_ids = list(
                dict.fromkeys(str(request.session_id) for request in batch)
            )
            tails = self._session_tails_for_append_group(c, ordered_ids)
            # Preserve the original first-unknown-session failure order before
            # any event is inserted. Known empty sessions remain present with
            # ``seq/hash`` set to NULL and therefore append at seq=1/GENESIS.
            for session_id in ordered_ids:
                if session_id not in tails:
                    raise KeyError(f"unknown session: {session_id}")

            persisted: list[RuntimeEvent] = []
            for request in batch:
                session_id = str(request.session_id)
                event = self._append_in_transaction(
                    c,
                    session_id,
                    request.event_type,
                    request.payload,
                    tail=tails[session_id],
                )
                persisted.append(event)
                tails[session_id] = {"seq": event.seq, "hash": event.hash}
            return persisted

    def _persist_bootstrap_group(
        self, batch: list[_GroupedBootstrap]
    ) -> list[tuple[list[RuntimeEvent], dict[str, Any]]]:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            persisted_groups: list[tuple[list[RuntimeEvent], dict[str, Any]]] = []
            for request in batch:
                c.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?,?)",
                    (
                        request.session_id,
                        request.parent_session_id,
                        request.parent_seq,
                        time.time(),
                        json.dumps(request.meta or {}, ensure_ascii=False, default=str),
                    ),
                )
                persisted: list[RuntimeEvent] = []
                tail: dict[str, Any] = {"seq": None, "hash": None}
                for event_type, payload in request.events:
                    event = self._append_in_transaction(
                        c,
                        request.session_id,
                        str(event_type),
                        payload,
                        tail=tail,
                    )
                    persisted.append(event)
                    tail = {"seq": event.seq, "hash": event.hash}
                reference = self._save_checkpoint_in_transaction(
                    c,
                    request.session_id,
                    request.snapshot,
                    tail=tail,
                )
                persisted_groups.append((persisted, reference))
            return persisted_groups

    def _persist_checkpoint_group(
        self, batch: list[_GroupedCheckpoint]
    ) -> list[tuple[dict[str, Any], RuntimeEvent]]:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            tails: dict[str, dict[str, Any] | Any] = {}
            persisted: list[tuple[dict[str, Any], RuntimeEvent]] = []
            for request in batch:
                if request.session_id not in tails:
                    tail = self._session_tail(c, request.session_id)
                    if tail is None:
                        raise KeyError(f"unknown session: {request.session_id}")
                    tails[request.session_id] = tail
                tail = tails[request.session_id]
                reference = self._save_checkpoint_in_transaction(
                    c,
                    request.session_id,
                    request.snapshot,
                    seq=request.seq,
                    tail=tail,
                )
                payload = {**request.event_payload, **reference}
                event = self._append_in_transaction(
                    c,
                    request.session_id,
                    request.event_type,
                    payload,
                    tail=tail,
                )
                persisted.append((reference, event))
                tails[request.session_id] = {"seq": event.seq, "hash": event.hash}
            return persisted
