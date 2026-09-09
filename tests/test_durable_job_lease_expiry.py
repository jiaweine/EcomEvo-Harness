from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from ecomevo.product.guarded_store import ConversationStore


def _claimed_turn(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    conv = store.create_conversation("lease-fence", "merchant_review")
    store.list_assets(conv["id"])
    token = store.claim_turn(conv["id"])
    assert token
    return store, conv, token


def _accept_job(store, conv, token):
    return store.accept_message_job(
        conv["id"],
        lease_token=token,
        content="审核商家",
        asset_ids=[],
        provider="demo",
        domain="merchant_review",
        history=[],
        asset_snapshot=[],
    )[2]


def test_expired_job_lease_cannot_be_renewed_by_stale_worker(tmp_path):
    store, conv, token = _claimed_turn(tmp_path)
    job = _accept_job(store, conv, token)
    assert store.claim_job("worker-old", job_id=job["id"], lease_seconds=60)

    with store._conn() as connection:
        connection.execute(
            "UPDATE conversation_jobs SET lease_until=? WHERE id=?",
            (time.time() - 1, job["id"]),
        )

    assert store.renew_job(job["id"], "worker-old", lease_seconds=60) is False
    claimed = store.claim_job("worker-new", job_id=job["id"], lease_seconds=60)
    assert claimed and claimed["worker_id"] == "worker-new"


def test_expired_turn_lease_cannot_accept_a_durable_job(tmp_path):
    store, conv, token = _claimed_turn(tmp_path)
    with store._conn() as connection:
        connection.execute(
            "UPDATE turn_leases SET expires_at=? WHERE conversation_id=?",
            (time.time() - 1, conv["id"]),
        )

    with pytest.raises(HTTPException) as exc:
        _accept_job(store, conv, token)
    assert exc.value.status_code == 409
    assert store.list_messages(conv["id"]) == []
    assert store.job_counts() == {}
