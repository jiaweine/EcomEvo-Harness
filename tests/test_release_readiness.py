from __future__ import annotations

from types import SimpleNamespace

import pytest

from ecomevo.product import ConversationStore
from ecomevo.product.deployment_topology import evaluate_deployment_topology
from ecomevo.product.release_readiness import ReleaseReadinessCenter


class FakeEvaluationStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def list_runs(self, limit=1):
        return self.rows[:limit]


class FakeEvaluationCenter:
    def __init__(self, rows=None):
        self.store = FakeEvaluationStore(rows)


class FakeRegistry:
    def __init__(self):
        self.servers = {}
        self.action_map = {}

    def read_tool_specs(self):
        return []


class FakePolicyStore:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def list_versions(self):
        return list(self.rows)


def make_center(tmp_path, *, eval_rows=None, deployment_nodes="1"):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    center = ReleaseReadinessCenter(
        tmp_path / "readiness.db",
        store=store,
        evaluation_center=FakeEvaluationCenter(eval_rows),
        mcp_registry=FakeRegistry(),
        policy_store=FakePolicyStore(),
        deployment_topology_provider=lambda: evaluate_deployment_topology(
            deployment_nodes,
            source="test",
        ),
    )
    return store, center


def passing_eval():
    return {
        "id": "eval-pass",
        "ok": True,
        "case_count": 6,
        "failed_case_count": 0,
        "drift_case_count": 0,
        "source_hash": "abc",
        "created_at": 100.0,
    }


def test_missing_gold_set_fails_closed_without_invented_thresholds(tmp_path):
    _store, center = make_center(tmp_path)

    preview = center.preview(tenant_id="tenant-a", window="7d", now=1000.0)

    assert preview["status"] == "blocked"
    assert preview["blocker_count"] == 1
    checks = {row["id"]: row for row in preview["checks"]}
    assert checks["gold_set_latest"]["status"] == "blocker"
    assert checks["deployment_topology"]["status"] == "pass"
    assert checks["uncertain_side_effects"]["status"] == "pass"
    assert checks["quality_sample"]["status"] == "warning"
    assert preview["methodology"]["success_rate_threshold"] is None
    assert preview["methodology"]["evidence_gap_threshold"] is None
    assert preview["authority"]["approved_for_release"] is False
    assert preview["authority"]["changes_production_authority"] is False
    assert preview["sources"]["connections"]["safety"]["configuration_mutation"] is False


def test_passing_gold_set_only_means_ready_for_human_review(tmp_path):
    _store, center = make_center(tmp_path, eval_rows=[passing_eval()])

    preview = center.preview(tenant_id="tenant-a", window="24h", now=1000.0)

    assert preview["status"] == "ready_for_human_release_review"
    assert preview["blocker_count"] == 0
    assert preview["warning_count"] == 1
    assert preview["authority"]["approved_for_release"] is False
    assert preview["methodology"]["readiness_means"].startswith("ready for human release review")


def test_deployment_topology_fails_closed_when_declaration_is_missing(tmp_path):
    _store, center = make_center(
        tmp_path,
        eval_rows=[passing_eval()],
        deployment_nodes=None,
    )

    preview = center.preview(tenant_id="tenant-a", now=1000.0)
    checks = {row["id"]: row for row in preview["checks"]}
    topology = preview["sources"]["deployment_topology"]

    assert preview["status"] == "blocked"
    assert checks["deployment_topology"]["status"] == "blocker"
    assert topology["declaration_present"] is False
    assert topology["actual_replica_discovery"] is False
    assert topology["release_supported"] is False


@pytest.mark.parametrize("raw_nodes", ["0", "-1", "not-a-number"])
def test_deployment_topology_rejects_invalid_declarations(tmp_path, raw_nodes):
    _store, center = make_center(
        tmp_path,
        eval_rows=[passing_eval()],
        deployment_nodes=raw_nodes,
    )

    preview = center.preview(tenant_id="tenant-a", now=1000.0)
    checks = {row["id"]: row for row in preview["checks"]}

    assert preview["status"] == "blocked"
    assert checks["deployment_topology"]["status"] == "blocker"
    assert preview["sources"]["deployment_topology"]["declaration_valid"] is False


def test_multi_node_declaration_is_a_hard_blocker_for_sqlite(tmp_path):
    _store, center = make_center(
        tmp_path,
        eval_rows=[passing_eval()],
        deployment_nodes="2",
    )

    preview = center.preview(tenant_id="tenant-a", now=1000.0)
    checks = {row["id"]: row for row in preview["checks"]}
    topology = preview["sources"]["deployment_topology"]

    assert preview["status"] == "blocked"
    assert checks["deployment_topology"]["status"] == "blocker"
    assert topology["declared_nodes"] == 2
    assert topology["certified_max_nodes"] == 1
    assert topology["same_node_multi_process_supported"] is True
    assert topology["cross_node_supported"] is False
    assert topology["requires_central_transactional_backend_for_multi_node"] is True


def _insert_open_feedback(store, cid, feedback_id, impact, created_at):
    with store._conn() as db:
        db.execute(
            """
            INSERT INTO evidence_disputes(
                id,conversation_id,assistant_message_id,submitted_by,category,impact,
                target_type,target_ref,target_snapshot,explanation,proposed_correction,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                feedback_id,
                cid,
                "assistant-x",
                "operator-a",
                "other",
                impact,
                "answer",
                "",
                "{}",
                "test",
                "",
                created_at,
            ),
        )


def test_action_blocking_feedback_is_a_hard_blocker(tmp_path):
    store, center = make_center(tmp_path, eval_rows=[passing_eval()])
    conv = store.create_conversation(
        "feedback",
        "aftersales",
        tenant_id="tenant-a",
        created_by="operator-a",
    )
    _insert_open_feedback(store, conv["id"], "fb-1", "action_blocking", 1.0)

    preview = center.preview(tenant_id="tenant-a", now=1000.0)
    checks = {row["id"]: row for row in preview["checks"]}

    assert preview["status"] == "blocked"
    assert checks["action_blocking_feedback"]["status"] == "blocker"
    assert preview["sources"]["feedback"]["action_blocking"] == 1
    assert preview["sources"]["feedback"]["exact_count"] is True


def test_old_action_blocker_is_not_hidden_by_large_feedback_queue(tmp_path):
    store, center = make_center(tmp_path, eval_rows=[passing_eval()])
    conv = store.create_conversation(
        "feedback-volume",
        "aftersales",
        tenant_id="tenant-a",
        created_by="operator-a",
    )
    _insert_open_feedback(store, conv["id"], "fb-old-blocker", "action_blocking", 1.0)
    for index in range(205):
        _insert_open_feedback(
            store,
            conv["id"],
            f"fb-new-{index}",
            "answer_only",
            100.0 + index,
        )

    preview = center.preview(tenant_id="tenant-a", now=1000.0)
    feedback = preview["sources"]["feedback"]

    assert feedback["open_count"] == 206
    assert feedback["action_blocking"] == 1
    assert feedback["answer_only"] == 205
    assert feedback["exact_count"] is True
    assert preview["status"] == "blocked"


def test_policy_inventory_is_tenant_scoped_without_fake_resolution(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    policies = FakePolicyStore(
        [
            SimpleNamespace(status="published", domain="aftersales", scope={"tenant": "tenant-a"}),
            SimpleNamespace(status="published", domain="risk_review", scope={"tenant": "tenant-b"}),
            SimpleNamespace(status="published", domain="general", scope={}),
        ]
    )
    center = ReleaseReadinessCenter(
        tmp_path / "readiness.db",
        store=store,
        evaluation_center=FakeEvaluationCenter([passing_eval()]),
        mcp_registry=FakeRegistry(),
        policy_store=policies,
    )

    preview = center.preview(tenant_id="tenant-a", now=1000.0)
    policy = preview["sources"]["policy"]

    assert policy["visible_versions"] == 2
    assert policy["resolution_evaluated"] is False
    assert "scope" in policy["reason"]


def test_snapshots_are_immutable_and_tenant_scoped(tmp_path):
    _store, center = make_center(tmp_path, eval_rows=[passing_eval()])

    first = center.create_snapshot(tenant_id="tenant-a", window="7d", now=1000.0)
    second = center.create_snapshot(tenant_id="tenant-a", window="7d", now=1001.0)

    assert first["id"] != second["id"]
    assert first["content_hash"] != second["content_hash"]
    assert center.get_snapshot(first["id"], tenant_id="tenant-a")["content_hash"] == first["content_hash"]
    with pytest.raises(KeyError):
        center.get_snapshot(first["id"], tenant_id="tenant-b")
    assert center.list_snapshots(tenant_id="tenant-b") == []
