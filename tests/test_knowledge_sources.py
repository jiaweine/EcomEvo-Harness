from __future__ import annotations

import concurrent.futures
import time

import pytest

from ecomevo.product.knowledge_sources import KnowledgeSourceStore


def _store(tmp_path):
    return KnowledgeSourceStore(tmp_path / "knowledge.db")


def _version(title="退款规则 v1", content=None):
    return {
        "title": title,
        "content_text": content or "售后退款必须核对订单、履约记录和适用规则；资料不足时应先补证据再做最终判断。",
        "effective_from": None,
        "effective_until": None,
        "review_due_at": time.time() + 86400,
        "provenance": "内部售后 SOP / governance test",
    }


def _source(store, tenant="tenant-a"):
    return store.create_source(
        tenant_id=tenant,
        actor_id="admin-a",
        name="售后退款知识库",
        source_tier="S2",
        domain="aftersales",
        description="受控售后规则与证据要求",
        owner="售后治理",
        jurisdiction="CN",
        tags=["退款", "履约", "退款"],
        version=_version(),
    )


def test_knowledge_versions_are_immutable_and_lifecycle_is_append_only(tmp_path):
    store = _store(tmp_path)
    source = _source(store)
    v1 = source["versions"][0]

    with pytest.raises(ValueError, match="only reviewed"):
        store.transition(
            v1["version_id"],
            "published",
            tenant_id="tenant-a",
            actor_id="admin-a",
        )

    reviewed = store.transition(
        v1["version_id"],
        "reviewed",
        tenant_id="tenant-a",
        actor_id="admin-a",
        note="治理复核通过",
    )
    assert reviewed["state"] == "reviewed"
    published = store.transition(
        v1["version_id"],
        "published",
        tenant_id="tenant-a",
        actor_id="admin-a",
        note="进入治理目录",
    )
    assert published["state"] == "published"
    assert [e["event_type"] for e in published["events"]] == ["created", "reviewed", "published"]

    with pytest.raises(ValueError, match="identical knowledge version"):
        store.create_version(
            source["source_id"],
            tenant_id="tenant-a",
            actor_id="admin-a",
            **_version(),
        )

    v2 = store.create_version(
        source["source_id"],
        tenant_id="tenant-a",
        actor_id="admin-a",
        **_version(
            title="退款规则 v2",
            content="退款规则第二版：必须核对订单、承运商履约轨迹和用户举证；关键事实冲突时必须升级人工复核。",
        ),
    )
    assert v2["version"] == 2
    assert v2["content_hash"] != v1["content_hash"]
    store.transition(v2["version_id"], "reviewed", tenant_id="tenant-a", actor_id="admin-a")
    v2 = store.transition(v2["version_id"], "published", tenant_id="tenant-a", actor_id="admin-a")
    assert v2["state"] == "published"
    assert store.get_version("tenant-a", v1["version_id"])["state"] == "superseded"

    with pytest.raises(ValueError, match="only the latest"):
        # A superseded immutable version cannot be silently republished as rollback.
        store.transition(v1["version_id"], "published", tenant_id="tenant-a", actor_id="admin-a")


def test_source_tier_boundary_rejects_s1_and_s3(tmp_path):
    store = _store(tmp_path)
    for tier in ("S1", "S3"):
        with pytest.raises(ValueError, match="only S2"):
            store.create_source(
                tenant_id="tenant-a",
                actor_id="admin-a",
                name="非法来源",
                source_tier=tier,
                domain="general",
                description="",
                owner="治理",
                jurisdiction="",
                tags=[],
                version=_version(),
            )


def test_tenant_isolation_search_and_projection_are_non_authoritative(tmp_path):
    store = _store(tmp_path)
    source_a = _source(store, "tenant-a")
    source_b = _source(store, "tenant-b")
    v_a = source_a["versions"][0]
    v_b = source_b["versions"][0]
    for tenant, version in (("tenant-a", v_a), ("tenant-b", v_b)):
        store.transition(version["version_id"], "reviewed", tenant_id=tenant, actor_id="admin")
        store.transition(version["version_id"], "published", tenant_id=tenant, actor_id="admin")

    assert store.get_source("tenant-a", source_b["source_id"]) is None
    assert store.get_version("tenant-a", v_b["version_id"]) is None
    assert {row["source_id"] for row in store.search_published("tenant-a", "退款")} == {source_a["source_id"]}

    projection = store.retrieval_projection("tenant-a", v_a["version_id"])
    assert projection["runtime_projection_status"] == "blocked_pending_explicit_source_integration_gate"
    assert projection["authority"] == {
        "changes_runtime_evidence": False,
        "changes_production_authority": False,
        "changes_policy": False,
        "changes_routing": False,
        "executes_tools": False,
        "grants_action_authority": False,
        "eligible_for_runtime_evidence": False,
        "s1_assignment_allowed": False,
        "s3_assignment_allowed": False,
        "open_web_unlocks_high_impact_actions": False,
    }


def test_only_published_current_versions_are_searchable(tmp_path):
    store = _store(tmp_path)
    source = _source(store)
    v1 = source["versions"][0]
    assert store.search_published("tenant-a", "退款") == []

    store.transition(v1["version_id"], "reviewed", tenant_id="tenant-a", actor_id="admin")
    store.transition(v1["version_id"], "published", tenant_id="tenant-a", actor_id="admin")
    hits = store.search_published("tenant-a", "退款")
    assert len(hits) == 1 and hits[0]["version_id"] == v1["version_id"]

    store.transition(v1["version_id"], "retired", tenant_id="tenant-a", actor_id="admin")
    assert store.search_published("tenant-a", "退款") == []


def test_concurrent_version_allocation_is_monotonic(tmp_path):
    store = _store(tmp_path)
    source = _source(store)

    def create(index):
        return store.create_version(
            source["source_id"],
            tenant_id="tenant-a",
            actor_id=f"admin-{index}",
            **_version(
                title=f"并发版本 {index}",
                content=f"并发版本 {index} 的受控知识内容，用于验证 BEGIN IMMEDIATE 下版本号不会冲突或覆盖。" * 2,
            ),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(create, range(6)))
    assert sorted(row["version"] for row in rows) == [2, 3, 4, 5, 6, 7]
    assert len({row["version_id"] for row in rows}) == 6
