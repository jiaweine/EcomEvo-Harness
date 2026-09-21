from __future__ import annotations

import pytest

from ecomevo.runtime.policy_control import PolicyStore
from ecomevo.runtime.policy_workflow import (
    PolicyWorkflow,
    PolicyWorkflowConflict,
    PolicyWorkflowStaleApproval,
)


def _workflow(tmp_path, *, seed_defaults=True):
    store = PolicyStore(tmp_path / "runtime.db", seed_defaults=seed_defaults)
    return store, PolicyWorkflow(store)


def _draft(workflow: PolicyWorkflow, *, tenant="A", actor="maker", key="refund-guard", value="review"):
    return workflow.create_draft(
        tenant_id=tenant,
        actor_id=actor,
        policy_key=key,
        domain="aftersales",
        rules=["证据不足时进入复核"],
        controls={"tenant.refund.action": value},
        scope={"channel": "marketplace"},
        authority=60,
        priority=5,
        source="internal-policy:test",
    )


def test_maker_checker_publish_and_retire_are_tenant_scoped(tmp_path):
    store, workflow = _workflow(tmp_path)
    draft = _draft(workflow)

    assert draft["status"] == "draft"
    assert draft["workflow_state"] == "draft"
    assert draft["scope"] == {"channel": "marketplace", "tenant": "A"}
    assert draft["authority"]["creator_can_self_approve"] is False
    assert draft["events"][0]["event_type"] == "draft_created"

    before = store.resolve(
        "aftersales",
        scope={"tenant": "A", "channel": "marketplace"},
        as_of="2030-01-01T00:00:00Z",
    )
    assert "tenant.refund.action" not in before["controls"]

    preview = workflow.preview_publish(
        draft["policy_id"], draft["version"], tenant_id="A", effective_from="2030-01-01T00:00:00Z"
    )
    assert preview["safe_to_apply"] is True
    assert preview["production_mutated"] is False
    assert store.get_version(draft["policy_id"], draft["version"]).status == "draft"

    with pytest.raises(PermissionError, match="self-approve"):
        workflow.approve(
            draft["policy_id"], draft["version"], tenant_id="A", actor_id="maker",
            effective_from="2030-01-01T00:00:00Z",
        )

    approved = workflow.approve(
        draft["policy_id"], draft["version"], tenant_id="A", actor_id="checker",
        effective_from="2030-01-01T00:00:00Z", note="four eyes",
    )
    assert approved["workflow_state"] == "approved"
    assert approved["latest_approval"]["actor_id"] == "checker"

    with pytest.raises(PermissionError, match="approving checker"):
        workflow.publish(draft["policy_id"], draft["version"], tenant_id="A", actor_id="maker")

    published = workflow.publish(
        draft["policy_id"], draft["version"], tenant_id="A", actor_id="checker"
    )
    assert published["status"] == "active"
    assert published["approver"] == "checker"
    assert published["events"][-1]["event_type"] == "published"

    resolved = store.resolve(
        "aftersales",
        scope={"tenant": "A", "channel": "marketplace"},
        as_of="2030-01-01T00:00:01Z",
    )
    assert resolved["status"] == "resolved"
    assert resolved["controls"]["tenant.refund.action"] == "review"

    request = workflow.request_retirement(
        draft["policy_id"], draft["version"], tenant_id="A", actor_id="maker",
        effective_to="2030-01-02T00:00:00Z", note="replace policy",
    )
    assert request["retirement_request"]["actor_id"] == "maker"
    assert request["preview"]["production_mutated"] is False

    with pytest.raises(PermissionError, match="self-approve retirement"):
        workflow.retire(draft["policy_id"], draft["version"], tenant_id="A", actor_id="maker")

    retired = workflow.retire(
        draft["policy_id"], draft["version"], tenant_id="A", actor_id="checker"
    )
    assert retired["status"] == "retired"
    assert retired["events"][-1]["event_type"] == "retired"

    after = store.resolve(
        "aftersales",
        scope={"tenant": "A", "channel": "marketplace"},
        as_of="2030-01-02T00:00:01Z",
    )
    assert after["status"] == "resolved"
    assert "tenant.refund.action" not in after["controls"]


def test_tenant_family_namespace_and_scope_spoof_are_blocked(tmp_path):
    _store, workflow = _workflow(tmp_path)
    a = _draft(workflow, tenant="A", key="shared-key")
    b = _draft(workflow, tenant="B", actor="maker-b", key="shared-key")
    assert a["policy_id"] != b["policy_id"]
    assert a["policy_id"].startswith("tenant.")

    with pytest.raises(PermissionError):
        workflow.describe(a["policy_id"], a["version"], tenant_id="B")
    with pytest.raises(PermissionError):
        workflow.create_draft(
            tenant_id="A",
            actor_id="maker",
            policy_key="spoof",
            domain="aftersales",
            rules=["rule"],
            controls={},
            scope={"tenant": "B"},
            source="internal:test",
        )
    with pytest.raises(PermissionError, match="builtin"):
        workflow.describe("builtin.aftersales", 1, tenant_id="A")


def test_equal_precedence_conflict_blocks_approval(tmp_path):
    store, workflow = _workflow(tmp_path, seed_defaults=False)
    existing_id = workflow.policy_id("A", "existing")
    store.create_version(
        policy_id=existing_id,
        domain="aftersales",
        rules=["existing"],
        controls={"refund.mode": "manual"},
        scope={"tenant": "A"},
        authority=60,
        priority=0,
        status="active",
        effective_from="2029-01-01T00:00:00Z",
        owner="system-test",
        approver="system-test",
        source="fixture",
    )
    candidate = workflow.create_draft(
        tenant_id="A",
        actor_id="maker",
        policy_key="candidate",
        domain="aftersales",
        rules=["candidate"],
        controls={"refund.mode": "auto"},
        scope={},
        authority=60,
        priority=0,
        source="internal:test",
    )
    preview = workflow.preview_publish(
        candidate["policy_id"], candidate["version"], tenant_id="A",
        effective_from="2030-01-01T00:00:00Z",
    )
    assert preview["safe_to_apply"] is False
    assert preview["resolution"]["status"] == "conflicted"
    assert preview["resolution"]["conflicts"][0]["control"] == "refund.mode"

    with pytest.raises(PolicyWorkflowConflict):
        workflow.approve(
            candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker",
            effective_from="2030-01-01T00:00:00Z",
        )
    assert store.get_version(candidate["policy_id"], candidate["version"]).status == "draft"


def test_environment_drift_invalidates_approval_until_reapproved(tmp_path):
    store, workflow = _workflow(tmp_path)
    candidate = _draft(workflow, key="drift-check")
    workflow.approve(
        candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker",
        effective_from="2030-01-01T00:00:00Z",
    )

    store.create_version(
        policy_id="environment-observer",
        domain="aftersales",
        rules=["environment changed"],
        controls={"environment.marker": True},
        scope={"tenant": "A"},
        authority=40,
        priority=0,
        status="active",
        effective_from="2029-01-01T00:00:00Z",
        owner="system-test",
        approver="system-test",
        source="fixture",
    )
    with pytest.raises(PolicyWorkflowStaleApproval, match="environment changed"):
        workflow.publish(candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker")

    workflow.approve(
        candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker-2",
        effective_from="2030-01-01T00:00:00Z",
    )
    published = workflow.publish(
        candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker-2"
    )
    assert published["status"] == "active"


def test_publish_rolls_back_if_atomic_audit_write_fails(tmp_path, monkeypatch):
    store, workflow = _workflow(tmp_path)
    candidate = _draft(workflow, key="atomic-audit")
    workflow.approve(
        candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker",
        effective_from="2030-01-01T00:00:00Z",
    )

    def explode(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(workflow, "_append_audit_in_transaction", explode)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        workflow.publish(candidate["policy_id"], candidate["version"], tenant_id="A", actor_id="checker")

    assert store.get_version(candidate["policy_id"], candidate["version"]).status == "draft"
