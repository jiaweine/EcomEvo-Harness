from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from ecomevo.product.guarded_store import ConversationStore


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
    store.release_turn(conv["id"], turn)
    assert not store.has_active_job(conv["id"])


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
        row = c.execute("SELECT status FROM conversation_jobs WHERE id=?", (job["id"],)).fetchone()
    assert row["status"] == "succeeded"
