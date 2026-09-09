from __future__ import annotations

import asyncio
import threading

from ecomevo.api.durable_jobs import DurableConversationWorker


class RecordingStore:
    def __init__(
        self,
        *,
        claim_result=None,
        renew_job_result: bool = False,
        turn_result: bool = False,
    ):
        self.claim_result = claim_result
        self.renew_job_result = renew_job_result
        self.turn_result = turn_result
        self.threads: dict[str, list[int]] = {}

    def _record(self, name: str) -> None:
        self.threads.setdefault(name, []).append(threading.get_ident())

    def claim_job(self, *_args, **_kwargs):
        self._record("claim_job")
        return self.claim_result

    def renew_job(self, *_args, **_kwargs):
        self._record("renew_job")
        return self.renew_job_result

    def renew_or_restore_turn(self, *_args, **_kwargs):
        self._record("renew_or_restore_turn")
        return self.turn_result

    def finish_job_success(self, *_args, **_kwargs):
        self._record("finish_job_success")
        return {"id": 1}

    def finish_job_failure(self, *_args, **_kwargs):
        self._record("finish_job_failure")
        return None


class SuccessAnalyzer:
    async def run(self, **_kwargs):
        return {
            "actions": [],
            "session_id": "session-pressure",
            "domain": "merchant_review",
            "runtime": {},
            "answer": "done",
        }


class FailureAnalyzer:
    async def run(self, **_kwargs):
        raise RuntimeError("injected terminal failure")


class NoopMCP:
    def action_binding(self, *_args, **_kwargs):
        return None


async def _unused_emit(*_args, **_kwargs):
    raise AssertionError("durable worker store-offload gate must not emit")


def _worker(store: RecordingStore, analyzer=None) -> DurableConversationWorker:
    return DurableConversationWorker(
        store,
        analyzer,
        NoopMCP(),
        emit=_unused_emit,
        wake=lambda _cid: None,
    )


def _job() -> dict:
    return {
        "id": "job-pressure",
        "conversation_id": "conversation-pressure",
        "payload": {
            "lease_token": "lease-pressure",
            "content": "pressure",
            "provider": "demo",
            "domain": "merchant_review",
            "history": [],
            "assets": [],
        },
    }


def _assert_off_loop(store: RecordingStore, name: str, loop_thread: int) -> None:
    threads = store.threads.get(name) or []
    assert threads
    assert all(thread_id != loop_thread for thread_id in threads)


def test_claim_and_periodic_lease_io_run_off_event_loop():
    async def exercise():
        loop_thread = threading.get_ident()

        claim_store = RecordingStore()
        assert await _worker(claim_store).run_once() is False
        _assert_off_loop(claim_store, "claim_job", loop_thread)

        renew_store = RecordingStore(renew_job_result=True, turn_result=False)
        worker = _worker(renew_store)
        worker.renew_interval_seconds = 0.001
        lease_lost = asyncio.Event()
        await worker._renew(_job(), asyncio.Event(), lease_lost)
        assert lease_lost.is_set()
        _assert_off_loop(renew_store, "renew_job", loop_thread)
        _assert_off_loop(renew_store, "renew_or_restore_turn", loop_thread)

    asyncio.run(exercise())


def test_start_ownership_fences_run_off_event_loop():
    async def exercise():
        loop_thread = threading.get_ident()

        job_store = RecordingStore(renew_job_result=False)
        await _worker(job_store)._execute(_job())
        _assert_off_loop(job_store, "renew_job", loop_thread)

        turn_store = RecordingStore(renew_job_result=True, turn_result=False)
        await _worker(turn_store)._execute(_job())
        _assert_off_loop(turn_store, "renew_job", loop_thread)
        _assert_off_loop(turn_store, "renew_or_restore_turn", loop_thread)
        _assert_off_loop(turn_store, "finish_job_failure", loop_thread)

    asyncio.run(exercise())


def test_terminal_success_and_failure_commits_run_off_event_loop():
    async def exercise():
        loop_thread = threading.get_ident()

        success_store = RecordingStore(renew_job_result=True, turn_result=True)
        await _worker(success_store, SuccessAnalyzer())._execute(_job())
        _assert_off_loop(success_store, "finish_job_success", loop_thread)

        failure_store = RecordingStore(renew_job_result=True, turn_result=True)
        await _worker(failure_store, FailureAnalyzer())._execute(_job())
        _assert_off_loop(failure_store, "finish_job_failure", loop_thread)

    asyncio.run(exercise())
