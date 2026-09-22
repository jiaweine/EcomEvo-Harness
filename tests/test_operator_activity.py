from __future__ import annotations

from ecomevo.product import ConversationStore
from ecomevo.product.operator_activity import OperatorActivityLedger
from ecomevo.product.operator_observability import OperatorAwareQualityObservability


def _store(tmp_path):
    return ConversationStore(tmp_path / "operator-activity.db", tmp_path / "assets")


def _set_measurement_started_at(store, value: float) -> None:
    with store._conn() as db:
        db.execute(
            "UPDATE operator_activity_meta SET measurement_started_at=? WHERE singleton=1",
            (float(value),),
        )


def test_operator_activity_dedupes_tabs_and_is_tenant_scoped(tmp_path):
    store = _store(tmp_path)
    ledger = OperatorActivityLedger(store)
    base = 1_800_000_000.0

    # Same tenant/user/15-second slot: repeated heartbeats collapse to one bucket.
    ledger.record_heartbeat(tenant_id="tenant-a", user_id="user-a", now=base + 1)
    ledger.record_heartbeat(tenant_id="tenant-a", user_id="user-a", now=base + 8)
    # Next slot counts once more.
    ledger.record_heartbeat(tenant_id="tenant-a", user_id="user-a", now=base + 16)
    # A second operator contributes person-time independently.
    ledger.record_heartbeat(tenant_id="tenant-a", user_id="user-b", now=base + 1)
    # Other tenant must not enter tenant-a aggregation.
    ledger.record_heartbeat(tenant_id="tenant-b", user_id="user-z", now=base + 1)

    tenant_a = ledger.summarize(tenant_id="tenant-a", since=base, until=base + 60)
    tenant_b = ledger.summarize(tenant_id="tenant-b", since=base, until=base + 60)

    assert tenant_a["bucket_count"] == 3
    assert tenant_a["active_seconds"] == 45
    assert tenant_a["operator_hours"] == 0.0125
    assert tenant_a["active_users"] == 2
    assert tenant_a["client_duration_accepted"] is False
    assert tenant_a["changes_authority"] is False

    assert tenant_b["bucket_count"] == 1
    assert tenant_b["active_seconds"] == 15
    assert tenant_b["active_users"] == 1


def test_first_bucket_overlapping_measurement_start_is_counted(tmp_path):
    store = _store(tmp_path)
    ledger = OperatorActivityLedger(store)
    base = 1_800_000_000.0
    _set_measurement_started_at(store, base + 7)

    # The durable bucket starts at base, but the actual heartbeat arrives after
    # measurement begins. Bucket-granularity storage must not erase that activity.
    ledger.record_heartbeat(
        tenant_id="tenant-a",
        user_id="operator-a",
        now=base + 8,
    )

    summary = ledger.summarize(
        tenant_id="tenant-a",
        since=base - 60,
        until=base + 30,
    )
    assert summary["window_fully_covered"] is False
    assert summary["active_seconds"] == 15
    assert summary["bucket_count"] == 1


def test_read_only_operator_observability_probe_does_not_create_schema(tmp_path):
    store = _store(tmp_path)
    ledger = OperatorActivityLedger(store, ensure_schema=False)

    assert ledger.instrumented() is False
    summary = ledger.summarize(tenant_id="tenant-a", since=0, until=60)
    assert summary["instrumented"] is False

    with store._conn() as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_active_blocks'"
        ).fetchone()
    assert table is None


def test_vdph_uses_exact_active_seconds_not_rounded_hours(tmp_path):
    store = _store(tmp_path)
    ledger = OperatorActivityLedger(store)
    base = 1_800_000_000.0
    _set_measurement_started_at(store, base - 2 * 24 * 60 * 60)

    conv = store.create_conversation(
        "A",
        "aftersales",
        tenant_id="tenant-a",
        created_by="operator-a",
    )
    assistant = store.add_message(
        conv["id"],
        "assistant",
        "结果",
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "grounding": {"evidence_sufficiency": "sufficient"},
        },
    )
    with store._conn() as db:
        db.execute(
            "UPDATE conversations SET created_at=?,updated_at=? WHERE id=?",
            (base + 1, base + 5, conv["id"]),
        )
        db.execute(
            "UPDATE messages SET created_at=? WHERE id=?",
            (base + 5, assistant["id"]),
        )

    ledger.record_heartbeat(
        tenant_id="tenant-a",
        user_id="operator-a",
        now=base + 30,
    )

    snapshot = OperatorAwareQualityObservability(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=base + 60,
    )

    assert snapshot["north_star"]["verified_decisions"] == 1
    assert snapshot["north_star"]["operator_hours"] == {
        "available": True,
        "value": 0.004167,
        "active_seconds": 15,
        "active_users": 1,
        "bucket_seconds": 15,
        "coverage_complete": True,
        "coverage_rate": 1.0,
        "coverage_seconds": 86400.0,
        "measurement_started_at": base - 2 * 24 * 60 * 60,
        "definition": snapshot["operator_activity"]["definition"],
    }
    vdph = snapshot["north_star"]["verified_decisions_per_operator_hour"]
    assert vdph["available"] is True
    assert vdph["value"] == 240.0
    assert snapshot["telemetry_availability"]["operator_active_hours"]["available"] is True
    assert snapshot["telemetry_availability"]["operator_active_hours"]["complete"] is True
    assert snapshot["methodology"]["operator_active_hours_client_duration_accepted"] is False
    assert "not payroll/timekeeping evidence" in snapshot["methodology"]["operator_active_hours_limitation"]

    day = next(row for row in snapshot["series"] if row["verified_decisions"] == 1)
    assert day["operator_active_seconds"] == 15
    assert day["operator_hours"] == 0.004167
    assert day["verified_decisions_per_operator_hour"] == 240.0


def test_partial_window_coverage_withholds_full_window_vdph(tmp_path):
    store = _store(tmp_path)
    ledger = OperatorActivityLedger(store)
    base = 1_800_000_000.0
    _set_measurement_started_at(store, base + 30)

    conv = store.create_conversation(
        "partial",
        "aftersales",
        tenant_id="tenant-a",
        created_by="operator-a",
    )
    assistant = store.add_message(
        conv["id"],
        "assistant",
        "结果",
        {
            "runtime": {"status": "completed", "belief": {"missing_evidence": []}},
            "grounding": {"evidence_sufficiency": "sufficient"},
        },
    )
    with store._conn() as db:
        db.execute(
            "UPDATE conversations SET created_at=?,updated_at=? WHERE id=?",
            (base + 1, base + 5, conv["id"]),
        )
        db.execute(
            "UPDATE messages SET created_at=? WHERE id=?",
            (base + 5, assistant["id"]),
        )
    ledger.record_heartbeat(
        tenant_id="tenant-a",
        user_id="operator-a",
        now=base + 45,
    )

    snapshot = OperatorAwareQualityObservability(store).snapshot(
        tenant_id="tenant-a",
        window="24h",
        now=base + 60,
    )

    hours = snapshot["north_star"]["operator_hours"]
    assert hours["available"] is True
    assert hours["active_seconds"] == 15
    assert hours["coverage_complete"] is False
    assert 0.0 < hours["coverage_rate"] < 1.0

    vdph = snapshot["north_star"]["verified_decisions_per_operator_hour"]
    assert snapshot["north_star"]["verified_decisions"] == 1
    assert vdph["available"] is False
    assert "partial denominator" in vdph["reason"]
    assert snapshot["telemetry_availability"]["operator_active_hours"]["complete"] is False

    day = next(row for row in snapshot["series"] if row["verified_decisions"] == 1)
    assert day["operator_hours"] == 0.004167
    assert day["operator_coverage_complete"] is False
    assert day["verified_decisions_per_operator_hour"] is None


def test_zero_active_time_is_instrumented_but_vdph_stays_unavailable(tmp_path):
    store = _store(tmp_path)
    OperatorActivityLedger(store)
    _set_measurement_started_at(store, 1_800_000_000.0 - 2 * 24 * 60 * 60)

    snapshot = OperatorAwareQualityObservability(store).snapshot(
        tenant_id="tenant-empty",
        window="24h",
        now=1_800_000_000.0,
    )

    assert snapshot["north_star"]["operator_hours"]["available"] is True
    assert snapshot["north_star"]["operator_hours"]["value"] == 0.0
    assert snapshot["north_star"]["operator_hours"]["active_seconds"] == 0
    assert snapshot["north_star"]["verified_decisions_per_operator_hour"]["available"] is False
    assert snapshot["telemetry_availability"]["operator_active_hours"]["available"] is True
