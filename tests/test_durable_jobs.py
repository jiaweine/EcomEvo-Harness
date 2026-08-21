from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from ecomevo.product.guarded_store import ConversationStore
from ecomevo.api.durable_jobs import DurableConversationWorker


def make_store(tmp_path):
    return ConversationStore(tmp_path / "product.db", tmp_path / "assets")


def test_durable_job_is_persisted_and_reclaimable_after_worker_lease_expiry(tmp_path):
    store = make_store(tmp_path)
    conv = store.create_conversation("durable", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"], ttl=120)
    assert turn
    user, accepted, job = store.accept_message_job(
        conv["id"], lease_token=turn, content="审核商家", asset_ids=[], provider="demo",
        domain="merchant_review", history=[], asset_snapshot=[],
    )
    assert user["id"] == job["message_id"]
    assert accepted["payload"]["job_id"] == job["id"]
    assert store.has_active_job(conv["id"])
    first = store.claim_job("worker-a", job_id=job["id"], lease_seconds=60)
    assert first and first["attempts"] == 1 and first["status"] == "running"
    assert store.claim_job("worker-b", job_id=job["id"], lease_seconds=60) is None

    with store._conn() as c:
        c.execute("UPDATE conversation_jobs SET lease_until=? WHERE id=?", (time.time() - 1, job["id"]))
    second = store.claim_job("worker-b", job_id=job["id"], lease_seconds=60)
    assert second and second["attempts"] == 2 and second["worker_id"] == "worker-b"


def test_active_durable_job_blocks_new_turn_upload_and_false_interruption(tmp_path):
    store = make_store(tmp_path)
    conv = store.create_conversation("durable", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"], ttl=1)
    _, _, job = store.accept_message_job(
        conv["id"], lease_token=turn, content="审核商家", asset_ids=[], provider="demo",
        domain="merchant_review", history=[], asset_snapshot=[],
    )
    with store._conn() as c:
        c.execute("UPDATE turn_leases SET expires_at=? WHERE conversation_id=?", (time.time() - 1, conv["id"]))
    assert store.recover_interrupted_turn(conv["id"]) is None
    assert store.claim_turn(conv["id"]) is None

    path = tmp_path / "assets" / "x.txt"
    path.write_text("new evidence", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        store.add_asset(conv["id"], name="x.txt", mime="text/plain", path=str(path), size=12, meta={"kind": "text"})
    assert exc.value.status_code == 409

    claimed = store.claim_job("worker", job_id=job["id"])
    event = store.finish_job_failure(job["id"], worker_id="worker", message="failed", detail="terminal")
    assert claimed and event and event["type"] == "answer.error"
    assert not store.has_active_job(conv["id"])
    assert not store.has_active_turn(conv["id"])


def test_finish_job_success_atomically_persists_assistant_and_terminal_event(tmp_path):
    store = make_store(tmp_path)
    conv = store.create_conversation("durable", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"])
    _, _, job = store.accept_message_job(
        conv["id"], lease_token=turn, content="审核商家", asset_ids=[], provider="demo",
        domain="merchant_review", history=[], asset_snapshot=[],
    )
    assert store.claim_job("worker", job_id=job["id"])
    result = {"answer": "done", "session_id": "run-x", "domain": "merchant_review", "actions": []}
    completed = store.finish_job_success(
        job["id"], worker_id="worker", session_id="run-x", actions=[], answer="done", result=result,
    )
    assert completed
    messages = store.list_messages(conv["id"])
    assert [row["role"] for row in messages] == ["user", "assistant"]
    events = store.list_events(conv["id"])
    assert [row["type"] for row in events] == ["message.accepted", "answer.ready"]
    with store._conn() as c:
        row = c.execute("SELECT status,session_id FROM conversation_jobs WHERE id=?", (job["id"],)).fetchone()
    assert row["status"] == "succeeded"
    assert row["session_id"] == "run-x"
    assert not store.has_active_turn(conv["id"])


def test_expired_worker_is_fenced_from_progress_and_terminal_commits(tmp_path):
    store = make_store(tmp_path)
    conv = store.create_conversation("expired", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"])
    _, _, job = store.accept_message_job(
        conv["id"], lease_token=turn, content="审核商家", asset_ids=[], provider="demo",
        domain="merchant_review", history=[], asset_snapshot=[],
    )
    assert store.claim_job("worker-old", job_id=job["id"])
    with store._conn() as c:
        c.execute(
            "UPDATE conversation_jobs SET lease_until=? WHERE id=?",
            (time.time() - 1, job["id"]),
        )

    assert store.add_job_event(job["id"], "worker-old", "progress", {"stale": True}) is None
    assert store.finish_job_success(
        job["id"], worker_id="worker-old", session_id="stale", actions=[],
        answer="stale", result={"answer": "stale"},
    ) is None
    assert store.finish_job_failure(
        job["id"], worker_id="worker-old", message="stale", detail="stale",
    ) is None
    assert [event["type"] for event in store.list_events(conv["id"])] == ["message.accepted"]
    assert store.claim_job("worker-new", job_id=job["id"])


class HandoffAnalyzer:
    def __init__(self):
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()

    async def run(self, *, sink, **_kwargs):
        self.started.set()
        await self.proceed.wait()
        await sink("planning.progress", {"detail": "stale progress"})
        return {
            "actions": [],
            "session_id": "stale-session",
            "domain": "merchant_review",
            "runtime": {},
            "answer": "stale answer",
        }


class CancellableAnalyzer:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, **_kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class NoopMCP:
    def action_binding(self, *_args, **_kwargs):
        return None


async def fenced_emit(store, cid, event_type, payload, job_id=None, worker_id=None):
    if job_id and worker_id:
        return store.add_job_event(job_id, worker_id, event_type, payload)
    return store.add_event(cid, event_type, payload)


def make_claimed_job(store, worker_id):
    conv = store.create_conversation("handoff", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"])
    assert turn
    _, _, job = store.accept_message_job(
        conv["id"], lease_token=turn, content="审核商家", asset_ids=[], provider="demo",
        domain="merchant_review", history=[], asset_snapshot=[],
    )
    claimed = store.claim_job(worker_id, job_id=job["id"])
    assert claimed
    return conv, turn, claimed


def force_job_handoff(store, job_id, new_worker_id):
    with store._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            "UPDATE conversation_jobs SET worker_id=?,lease_until=?,attempts=attempts+1 "
            "WHERE id=? AND status='running'",
            (new_worker_id, time.time() + 60, job_id),
        )
        assert cur.rowcount == 1
        claimed = c.execute("SELECT * FROM conversation_jobs WHERE id=?", (job_id,)).fetchone()
    assert claimed and claimed["worker_id"] == new_worker_id
    return dict(claimed)


@pytest.mark.asyncio
async def test_stale_worker_cannot_emit_progress_or_release_turn_after_handoff(tmp_path):
    store = make_store(tmp_path)
    analyzer = HandoffAnalyzer()
    worker = DurableConversationWorker(
        store, analyzer, NoopMCP(),
        emit=lambda cid, event_type, payload, job_id=None, worker_id=None: fenced_emit(
            store, cid, event_type, payload, job_id, worker_id
        ),
        wake=lambda _cid: None,
    )
    worker.renew_interval_seconds = 3600
    conv, token, job = make_claimed_job(store, worker.worker_id)

    task = asyncio.create_task(worker._execute(job))
    await asyncio.wait_for(analyzer.started.wait(), timeout=1)
    force_job_handoff(store, job["id"], "worker-new")
    analyzer.proceed.set()
    await asyncio.wait_for(task, timeout=1)

    assert [event["type"] for event in store.list_events(conv["id"])] == ["message.accepted"]
    with store._conn() as c:
        lease = c.execute(
            "SELECT token FROM turn_leases WHERE conversation_id=?", (conv["id"],)
        ).fetchone()
    assert lease and lease["token"] == token


@pytest.mark.asyncio
async def test_worker_cancels_analysis_when_renewal_detects_lease_loss(tmp_path):
    store = make_store(tmp_path)
    analyzer = CancellableAnalyzer()
    worker = DurableConversationWorker(
        store, analyzer, NoopMCP(),
        emit=lambda cid, event_type, payload, job_id=None, worker_id=None: fenced_emit(
            store, cid, event_type, payload, job_id, worker_id
        ),
        wake=lambda _cid: None,
    )
    worker.renew_interval_seconds = 0.01
    conv, token, job = make_claimed_job(store, worker.worker_id)

    task = asyncio.create_task(worker._execute(job))
    await asyncio.wait_for(analyzer.started.wait(), timeout=1)
    force_job_handoff(store, job["id"], "worker-new")
    await asyncio.wait_for(task, timeout=1)

    assert analyzer.cancelled.is_set()
    assert [event["type"] for event in store.list_events(conv["id"])] == ["message.accepted"]
    with store._conn() as c:
        lease = c.execute(
            "SELECT token FROM turn_leases WHERE conversation_id=?", (conv["id"],)
        ).fetchone()
    assert lease and lease["token"] == token


@pytest.mark.asyncio
async def test_worker_cancels_analysis_when_lease_renewal_errors(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    analyzer = CancellableAnalyzer()
    worker = DurableConversationWorker(
        store, analyzer, NoopMCP(),
        emit=lambda cid, event_type, payload, job_id=None, worker_id=None: fenced_emit(
            store, cid, event_type, payload, job_id, worker_id
        ),
        wake=lambda _cid: None,
    )
    worker.renew_interval_seconds = 0.01
    conv, token, job = make_claimed_job(store, worker.worker_id)
    original_renew = store.renew_job
    calls = 0

    def fail_after_initial_fence(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_renew(*args, **kwargs)
        raise RuntimeError("injected lease database failure")

    monkeypatch.setattr(store, "renew_job", fail_after_initial_fence)
    task = asyncio.create_task(worker._execute(job))
    await asyncio.wait_for(analyzer.started.wait(), timeout=1)
    await asyncio.wait_for(task, timeout=1)

    assert analyzer.cancelled.is_set()
    assert [event["type"] for event in store.list_events(conv["id"])] == ["message.accepted"]
    with store._conn() as c:
        lease = c.execute(
            "SELECT token FROM turn_leases WHERE conversation_id=?", (conv["id"],)
        ).fetchone()
    assert lease and lease["token"] == token


@pytest.mark.asyncio
async def test_worker_loop_retries_after_transient_claim_error():
    stop = asyncio.Event()

    class FlakyClaimStore:
        def __init__(self):
            self.calls = 0

        def claim_job(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected claim failure")
            stop.set()
            return None

    async def unused_emit(*_args, **_kwargs):
        raise AssertionError("no job should execute")

    flaky = FlakyClaimStore()
    worker = DurableConversationWorker(
        flaky, analyzer=None, mcp=None,
        emit=unused_emit,
        wake=lambda _cid: None,
    )
    worker.poll_seconds = 0.01

    await asyncio.wait_for(worker.loop(stop), timeout=1)

    assert flaky.calls == 2
