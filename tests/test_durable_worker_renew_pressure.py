from __future__ import annotations

import asyncio
import threading

from ecomevo.api.durable_jobs import DurableConversationWorker


class RecordingStore:
    def __init__(self, *, renew_job_result: bool = False):
        self.renew_job_result = renew_job_result
        self.threads: dict[str, int] = {}

    def _record(self, name: str) -> None:
        self.threads[name] = threading.get_ident()

    def claim_job(self, *_args, **_kwargs):
        self._record("claim_job")
        return None

    def renew_job(self, *_args, **_kwargs):
        self._record("renew_job")
        return self.renew_job_result

    def renew_or_restore_turn(self, *_args, **_kwargs):
        self._record("renew_or_restore_turn")
        return False

    def finish_job_failure(self, *_args, **_kwargs):
        return None


async def _unused_emit(*_args, **_kwargs):
    raise AssertionError("durable worker store-I/O gate must not emit")


def _worker(store: RecordingStore) -> DurableConversationWorker:
    return DurableConversationWorker(
        store,
        analyzer=None,
        mcp=None,
        emit=_unused_emit,
        wake=lambda _cid: None,
    )


def _job() -> dict:
    return {
        "id": "job-pressure",
        "conversation_id": "conversation-pressure",
        "payload": {"lease_token": "lease-pressure"},
    }


def _assert_off_loop(store: RecordingStore, name: str, loop_thread: int) -> None:
    assert store.threads.get(name) is not None
    assert store.threads[name] != loop_thread


def test_durable_claim_runs_off_event_loop():
    async def exercise():
        store = RecordingStore()
        loop_thread = threading.get_ident()
        assert await _worker(store).run_once() is False
        _assert_off_loop(store, "claim_job", loop_thread)

    asyncio.run(exercise())


def test_durable_renew_job_fence_runs_off_event_loop():
    async def exercise():
        store = RecordingStore(renew_job_result=False)
        worker = _worker(store)
        worker.renew_interval_seconds = 0.001
        lease_lost = asyncio.Event()
        await worker._renew(_job(), asyncio.Event(), lease_lost)
        assert lease_lost.is_set()
        _assert_off_loop(store, "renew_job", threading.get_ident())

    asyncio.run(exercise())


def test_durable_start_fences_run_off_event_loop():
    async def exercise():
        loop_thread = threading.get_ident()

        job_store = RecordingStore(renew_job_result=False)
        await _worker(job_store)._execute(_job())
        _assert_off_loop(job_store, "renew_job", loop_thread)

        turn_store = RecordingStore(renew_job_result=True)
        await _worker(turn_store)._execute(_job())
        _assert_off_loop(turn_store, "renew_job", loop_thread)
        _assert_off_loop(turn_store, "renew_or_restore_turn", loop_thread)

    asyncio.run(exercise())


def test_durable_periodic_turn_renewal_runs_off_event_loop():
    async def exercise():
        store = RecordingStore(renew_job_result=True)
        worker = _worker(store)
        worker.renew_interval_seconds = 0.001
        lease_lost = asyncio.Event()
        await worker._renew(_job(), asyncio.Event(), lease_lost)
        assert lease_lost.is_set()
        loop_thread = threading.get_ident()
        _assert_off_loop(store, "renew_job", loop_thread)
        _assert_off_loop(store, "renew_or_restore_turn", loop_thread)

    asyncio.run(exercise())
