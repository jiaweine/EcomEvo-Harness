from __future__ import annotations

import time

from ecomevo.product.guarded_store import ConversationStore


def test_expired_job_lease_cannot_be_renewed_by_stale_worker(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    conv = store.create_conversation("expired-renew", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"])
    assert turn
    _, _, job = store.accept_message_job(
        conv["id"],
        lease_token=turn,
        content="审核商家",
        asset_ids=[],
        provider="demo",
        domain="merchant_review",
        history=[],
        asset_snapshot=[],
    )
    assert store.claim_job("worker-old", job_id=job["id"], lease_seconds=60)

    with store._conn() as connection:
        connection.execute(
            "UPDATE conversation_jobs SET lease_until=? WHERE id=?",
            (time.time() - 1, job["id"]),
        )

    assert store.renew_job(job["id"], "worker-old", lease_seconds=60) is False
    claimed = store.claim_job("worker-new", job_id=job["id"], lease_seconds=60)
    assert claimed and claimed["worker_id"] == "worker-new"
