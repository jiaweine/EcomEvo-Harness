from __future__ import annotations

import asyncio
import threading
import time

from ecomevo.api.durable_jobs import DurableConversationWorker


class TerminalStore:
    def __init__(self, *, turn_owned: bool = True):
        self.turn_owned = turn_owned
        self.success_thread: int | None = None
        self.failure_thread: int | None = None

    def renew_job(self, *_args, **_kwargs):
        return True

    def renew_or_restore_turn(self, *_args, **_kwargs):
        return self.turn_owned

    def finish_job_success(self, *_args, **_kwargs):
        self.success_thread = threading.get_ident()
        time.sleep(0.08)
        return {"id": 1}

    def finish_job_failure(self, *_args, **_kwargs):
        self.failure_thread = threading.get_ident()
        time.sleep(0.08)
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
    raise AssertionError("terminal persistence pressure test must not emit")


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


def _worker(store: TerminalStore, analyzer) -> DurableConversationWorker:
    return DurableConversationWorker(
        store,
        analyzer,
        NoopMCP(),
        emit=_unused_emit,
        wake=lambda _cid: None,
    )


def test_terminal_success_commit_runs_off_event_loop():
    async def exercise():
        store = TerminalStore()
        loop_thread = threading.get_ident()
        await _worker(store, SuccessAnalyzer())._execute(_job())
        assert store.success_thread is not None
        assert store.success_thread != loop_thread

    asyncio.run(exercise())


def test_terminal_failure_commit_runs_off_event_loop():
    async def exercise():
        store = TerminalStore()
        loop_thread = threading.get_ident()
        await _worker(store, FailureAnalyzer())._execute(_job())
        assert store.failure_thread is not None
        assert store.failure_thread != loop_thread

    asyncio.run(exercise())


def test_turn_loss_failure_commit_runs_off_event_loop():
    async def exercise():
        store = TerminalStore(turn_owned=False)
        loop_thread = threading.get_ident()
        await _worker(store, SuccessAnalyzer())._execute(_job())
        assert store.failure_thread is not None
        assert store.failure_thread != loop_thread
        assert store.success_thread is None

    asyncio.run(exercise())
