from __future__ import annotations

import sqlite3
import time

from ecomevo.product.guarded_store import ConversationStore


def make_store(tmp_path):
    return ConversationStore(tmp_path / "product.db", tmp_path / "assets")


def make_job(store):
    conv = store.create_conversation("fencing", "merchant_review")
    store.list_assets(conv["id"])
    token = store.claim_turn(conv["id"], ttl=60)
    assert token
    _user, _accepted, job = store.accept_message_job(
        conv["id"],
        lease_token=token,
        content="审核商家",
        asset_ids=[],
        provider="demo",
        domain="merchant_review",
        history=[],
        asset_snapshot=[],
    )
    return conv, token, job


def test_turn_generation_advances_and_stale_generation_cannot_restore(tmp_path):
    store = make_store(tmp_path)
    conv = store.create_conversation("turn-fence", "merchant_review")
    store.list_assets(conv["id"])
    token_a = store.claim_turn(conv["id"], ttl=60)
    assert token_a
    with store._conn() as connection:
        first = connection.execute(
            "SELECT token,fence FROM turn_leases WHERE conversation_id=?",
            (conv["id"],),
        ).fetchone()
        connection.execute(
            "UPDATE turn_leases SET expires_at=? WHERE conversation_id=?",
            (time.time() - 1, conv["id"]),
        )
    assert first and int(first["fence"]) == 1

    store.list_assets(conv["id"])
    token_b = store.claim_turn(conv["id"], ttl=60)
    assert token_b and token_b != token_a
    with store._conn() as connection:
        second = connection.execute(
            "SELECT token,fence FROM turn_leases WHERE conversation_id=?",
            (conv["id"],),
        ).fetchone()
    assert second and int(second["fence"]) == 2

    assert store.renew_or_restore_turn(
        conv["id"], token_a, 60, fence=int(first["fence"])
    ) is False
    assert store.renew_or_restore_turn(
        conv["id"], token_b, 60, fence=int(second["fence"])
    ) is True


def test_accepted_job_binds_turn_fence(tmp_path):
    store = make_store(tmp_path)
    conv, token, job = make_job(store)

    with store._conn() as connection:
        turn = connection.execute(
            "SELECT token,fence FROM turn_leases WHERE conversation_id=?",
            (conv["id"],),
        ).fetchone()

    assert turn and turn["token"] == token
    assert int(job["payload"]["turn_fence"]) == int(turn["fence"]) == 1


def test_job_reclaim_advances_fence_even_when_worker_identity_is_reused(tmp_path):
    store = make_store(tmp_path)
    conv, _token, job = make_job(store)

    first = store.claim_job("worker-reused", job_id=job["id"], lease_seconds=60)
    assert first
    assert int(first["lease_fence"]) == 1

    with store._conn() as connection:
        connection.execute(
            "UPDATE conversation_jobs SET lease_until=? WHERE id=?",
            (time.time() - 1, job["id"]),
        )

    second = store.claim_job("worker-reused", job_id=job["id"], lease_seconds=60)
    assert second
    assert int(second["lease_fence"]) == 2
    assert second["worker_id"] == first["worker_id"] == "worker-reused"

    assert store.renew_job(
        job["id"],
        "worker-reused",
        60,
        lease_fence=int(first["lease_fence"]),
    ) is False
    assert store.add_job_event(
        job["id"],
        "worker-reused",
        "planning.progress",
        {"stale": True},
        lease_fence=int(first["lease_fence"]),
    ) is None
    assert store.finish_job_failure(
        job["id"],
        worker_id="worker-reused",
        lease_fence=int(first["lease_fence"]),
        message="stale",
        detail="stale generation",
    ) is None

    assert store.renew_job(
        job["id"],
        "worker-reused",
        60,
        lease_fence=int(second["lease_fence"]),
    ) is True
    fresh = store.add_job_event(
        job["id"],
        "worker-reused",
        "planning.progress",
        {"fresh": True},
        lease_fence=int(second["lease_fence"]),
    )
    assert fresh and fresh["type"] == "planning.progress"
    assert [row["payload"] for row in store.list_events(conv["id"]) if row["type"] == "planning.progress"] == [
        {"fresh": True}
    ]


def test_job_terminal_commit_requires_exact_fence(tmp_path):
    store = make_store(tmp_path)
    conv, _token, job = make_job(store)
    first = store.claim_job("worker-reused", job_id=job["id"], lease_seconds=60)
    assert first

    with store._conn() as connection:
        connection.execute(
            "UPDATE conversation_jobs SET lease_until=? WHERE id=?",
            (time.time() - 1, job["id"]),
        )
    second = store.claim_job("worker-reused", job_id=job["id"], lease_seconds=60)
    assert second

    stale = store.finish_job_success(
        job["id"],
        worker_id="worker-reused",
        lease_fence=int(first["lease_fence"]),
        session_id="stale-session",
        actions=[],
        answer="stale",
        result={"answer": "stale"},
    )
    assert stale is None
    assert [row["role"] for row in store.list_messages(conv["id"])] == ["user"]

    fresh = store.finish_job_success(
        job["id"],
        worker_id="worker-reused",
        lease_fence=int(second["lease_fence"]),
        session_id="fresh-session",
        actions=[],
        answer="fresh",
        result={"answer": "fresh"},
    )
    assert fresh
    assert [row["role"] for row in store.list_messages(conv["id"])] == ["user", "assistant"]


def test_lease_validity_does_not_use_application_wall_clock(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    conv = store.create_conversation("db-clock", "merchant_review")
    store.list_assets(conv["id"])
    token = store.claim_turn(conv["id"], ttl=60)
    assert token

    monkeypatch.setattr("ecomevo.product.guarded_store.time.time", lambda: 10**12)

    assert store.has_active_turn(conv["id"]) is True
    _user, _accepted, job = store.accept_message_job(
        conv["id"],
        lease_token=token,
        content="审核商家",
        asset_ids=[],
        provider="demo",
        domain="merchant_review",
        history=[],
        asset_snapshot=[],
    )
    assert job["payload"]["turn_fence"] == 1


def test_interrupted_turn_recovery_uses_transaction_clock(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    conv = store.create_conversation("recover-clock", "merchant_review")
    store.list_assets(conv["id"])
    token = store.claim_turn(conv["id"], ttl=60)
    assert token
    store.add_event(conv["id"], "message.accepted", {"manual": True})

    monkeypatch.setattr("ecomevo.product.guarded_store.time.time", lambda: 10**12)
    assert store.recover_interrupted_turn(conv["id"]) is None

    with store._conn() as connection:
        connection.execute(
            "UPDATE turn_leases SET expires_at=0 WHERE conversation_id=?",
            (conv["id"],),
        )
    recovered = store.recover_interrupted_turn(conv["id"])
    assert recovered and recovered["type"] == "answer.error"
    assert recovered["payload"]["recovered"] is True


def test_existing_lease_tables_are_migrated_with_fence_columns(tmp_path):
    db_path = tmp_path / "product.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE turn_leases(
                conversation_id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                expires_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE conversation_jobs(
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                worker_id TEXT,
                lease_until REAL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                session_id TEXT
            );
            """
        )

    store = ConversationStore(db_path, tmp_path / "assets")
    with store._conn() as connection:
        turn_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turn_leases)").fetchall()
        }
        job_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(conversation_jobs)").fetchall()
        }

    assert "fence" in turn_columns
    assert "lease_fence" in job_columns
