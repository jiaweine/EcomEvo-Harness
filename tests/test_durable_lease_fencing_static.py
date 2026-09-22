from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_guarded_store_uses_transaction_clock_and_persistent_fences():
    service = (ROOT / "ecomevo" / "product" / "guarded_store.py").read_text(encoding="utf-8")

    assert "lease_fence INTEGER NOT NULL DEFAULT 0" in service
    assert "ALTER TABLE conversation_jobs ADD COLUMN lease_fence INTEGER NOT NULL DEFAULT 0" in service
    assert "ALTER TABLE turn_leases ADD COLUMN fence INTEGER NOT NULL DEFAULT 0" in service
    assert "SELECT (julianday('now') - 2440587.5) * 86400.0 AS now" in service
    assert "lease_fence=lease_fence+1" in service
    assert "AND lease_fence=?" in service
    assert "AND fence=?" in service


def test_worker_propagates_exact_job_and_turn_fence():
    worker = (ROOT / "ecomevo" / "api" / "durable_jobs.py").read_text(encoding="utf-8")
    app = (ROOT / "ecomevo" / "api" / "application.py").read_text(encoding="utf-8")

    assert 'turn_fence = payload.get("turn_fence")' in worker
    assert 'lease_fence = int(job.get("lease_fence") or 0)' in worker
    assert "lease_fence=lease_fence" in worker
    assert "fence=turn_fence" in worker
    assert "lease_fence: int | None = None" in app
    assert "lease_fence=lease_fence" in app


def test_runtime_capability_does_not_claim_cross_node_support():
    service = (ROOT / "ecomevo" / "product" / "guarded_store.py").read_text(encoding="utf-8")
    readiness = (ROOT / "ecomevo" / "product" / "multi_node_readiness.py").read_text(encoding="utf-8")

    assert '"lease_clock": "sqlite_transaction_domain"' in service
    assert '"turn_lease_fencing_generation": True' in service
    assert '"job_lease_fencing_generation": True' in service
    assert '"cross_node_shared_transaction_domain": False' in service
    assert '"cross_node_supported": False' in service
    assert '"cross_node_lease_authority": False' in readiness
    assert '"local_lease_fencing_is_cross_node_certification": False' in readiness
