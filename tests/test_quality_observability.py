from __future__ import annotations

import json
import time

from ecomevo.product import ConversationStore
from ecomevo.product.observability import QualityObservability


def _store(tmp_path):
    return ConversationStore(tmp_path / "obs.db", tmp_path / "assets")


def _insert_job(db, *, jid, cid, status, created, updated, attempts=1):
    db.execute(
        "INSERT INTO conversation_jobs("
        "id,conversation_id,message_id,status,payload,worker_id,lease_until,attempts,last_error,created_at,updated_at,session_id"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            jid,
            cid,
            f"user-{jid}",
            status,
            "{}",
            None,
            None,
            attempts,
            None if status == "succeeded" else "failed",
            created,
            updated,
            f"session-{jid}",
        ),
    )


def _insert_action(db, *, aid, cid, status, created, requires=True, side_effect=True):
    db.execute(
        "INSERT INTO actions("
        "id,conversation_id,session_id,kind,title,description,risk_level,side_effect,requires_confirmation,status,payload,created_at,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            aid,
            cid,
            f"session-{aid}",
            "review",
            "test action",
            "test action",
            "high",
            int(side_effect),
            int(requires),
            status,
            "{}",
            created,
            created + 1,
        ),
    )


def _assistant(store, cid, created, payload):
    row = store.add_message(cid, "assistant", "结果", payload)
    with store._conn() as db:
        db.execute("UPDATE messages SET created_at=? WHERE id=?", (created, row["id"]))
    return row


def test_observability_uses_durable_facts_and_never_fakes_missing_telemetry(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    conv = store.create_conversation("A", "aftersales", tenant_id="tenant-a", created_by="admin-a")
    other = store.create_conversation("B", "risk_review", tenant_id="tenant-b", created_by="admin-b")
    with store._conn() as db:
        db.execute("UPDATE conversations SET created_at=?,updated_at=? WHERE id=?", (now - 500, now - 20, conv["id"]))
        db.execute("UPDATE conversations SET created_at=?,updated_at=? WHERE id=?", (now - 400, now - 20, other["id"]))
        _insert_job(db, jid="job-a-success", cid=conv["id"], status="succeeded", created=now - 100, updated=now - 80)
        _insert_job(db, jid="job-a-failed", cid=conv["id"], status="failed", created=now - 200, updated=now - 150, attempts=2)
        _insert_job(db, jid="job-b-success", cid=other["id"], status="succeeded", created=now - 90, updated=now - 70)
        _insert_action(db, aid="action-a-proposed", cid=conv["id"], status="proposed", created=now - 60)
        _insert_action(db, aid="action-a-uncertain", cid=conv["id"], status="uncertain", created=now - 50)
        _insert_action(db, aid="action-b-uncertain", cid=other["id"], status="uncertain", created=now - 40)

    _assistant(
        store,
        conv["id"],
        now - 75,
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "grounding": {"evidence_sufficiency": "sufficient"},
        },
    )
    _assistant(
        store,
        conv["id"],
        now - 45,
        {"runtime": {"status": "needs_evidence", "belief": {"missing_evidence": ["物流原始轨迹"]}}},
    )
    _assistant(
        store,
        other["id"],
        now - 30,
        {"runtime": {"status": "completed", "belief": {"missing_evidence": []}}},
    )

    snapshot = QualityObservability(store).snapshot(tenant_id="tenant-a", window="24h", now=now)

    assert snapshot["tenant_scope"] == "tenant-a"
    assert snapshot["reliability"]["jobs"] == 2
    assert snapshot["reliability"]["succeeded"] == 1
    assert snapshot["reliability"]["failed"] == 1
    assert snapshot["reliability"]["success_rate"] == 0.5
    assert snapshot["reliability"]["retry_jobs"] == 1
    assert snapshot["reliability"]["retry_rate"] == 0.5
    assert snapshot["reliability"]["end_to_end_latency_seconds"]["p50"] == 20.0
    assert snapshot["reliability"]["end_to_end_latency_seconds"]["p95"] == 50.0

    assert snapshot["quality"]["assistant_results"] == 2
    assert snapshot["quality"]["verified_decisions"] == 1
    assert snapshot["quality"]["verified_rate"] == 0.5
    assert snapshot["quality"]["evidence_gap_results"] == 1
    assert snapshot["quality"]["evidence_gap_rate"] == 0.5
    assert snapshot["quality"]["grounding_instrumented_results"] == 1
    assert snapshot["quality"]["grounding_coverage_rate"] == 0.5

    assert snapshot["authority_workload"]["actions_in_window"] == 2
    assert snapshot["authority_workload"]["current_waiting_approval"] == 1
    assert snapshot["authority_workload"]["current_uncertain_actions"] == 1
    assert snapshot["authority_workload"]["uncertain_incidents"] == 1
    assert snapshot["distribution"]["job_scenes"] == {"aftersales": 2}
    assert snapshot["distribution"]["new_conversation_scenes"] == {"aftersales": 1}

    assert snapshot["north_star"]["verified_decisions"] == 1
    assert snapshot["north_star"]["verified_decisions_per_operator_hour"]["available"] is False
    assert snapshot["telemetry_availability"]["token_usage"]["available"] is False
    assert snapshot["telemetry_availability"]["provider_cost"]["available"] is False
    assert snapshot["telemetry_availability"]["operator_active_hours"]["available"] is False
    assert snapshot["methodology"]["changes_authority"] is False
    json.dumps(snapshot, ensure_ascii=False)


def test_grounding_conflict_prevents_verified_classification():
    flags = QualityObservability._quality_flags(
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "grounding": {"evidence_sufficiency": "conflicted"},
        }
    )
    assert flags["verified"] is False
    assert flags["evidence_gap"] is True
    assert flags["has_grounding"] is True


def test_invalid_window_fails_closed(tmp_path):
    store = _store(tmp_path)
    service = QualityObservability(store)
    try:
        service.snapshot(tenant_id="tenant-a", window="90d")
    except ValueError as exc:
        assert "invalid observability window" in str(exc)
    else:
        raise AssertionError("invalid window must fail")
