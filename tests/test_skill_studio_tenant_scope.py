from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ecomevo.product.skill_studio import SkillStudioStore
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


def _studio(tmp_path: Path):
    skills = AdaptiveSkillLibrary(tmp_path / "runtime.db")
    return SkillStudioStore(tmp_path / "studio.db", skills, ToolRegistry()), skills


def _draft(name: str):
    return {
        "domain": "aftersales",
        "name": name,
        "purpose": "判责前核对订单、履约与用户证据",
        "guidance": "先检查订单和履约事实，再核对用户举证；关键事实不足时停止并请求补证，不得直接执行退款。",
        "preferred_tools": ["order.inspect", "evidence.search"],
        "trigger_terms": ["退款", "未收到货"],
        "input_contract": {"requires": ["order_context"]},
        "output_contract": {"fields": ["evidence_gaps", "recommendation"]},
        "safety_notes": "不授予动作审批或执行权限。",
        "source_skill_id": None,
    }


def test_studio_versions_are_tenant_isolated(tmp_path):
    studio, _ = _studio(tmp_path)
    created = studio.create_family(
        actor_id="admin-a",
        tenant_id="tenant-a",
        **_draft("tenant-a-procedure"),
    )

    assert studio.get_version(created["version_id"], tenant_id="tenant-a") is not None
    assert studio.get_version(created["version_id"], tenant_id="tenant-b") is None
    assert studio.latest_families(tenant_id="tenant-b") == []

    with pytest.raises(KeyError):
        studio.create_version(
            created["family_id"],
            actor_id="admin-b",
            tenant_id="tenant-b",
            **_draft("cross-tenant"),
        )
    with pytest.raises(KeyError):
        studio.submit(
            created["version_id"],
            actor_id="admin-b",
            tenant_id="tenant-b",
        )


def test_evaluation_and_release_candidate_are_tenant_scoped_and_read_only(
    tmp_path,
    monkeypatch,
):
    studio, skills = _studio(tmp_path)
    runtime_before = skills.snapshot()
    created = studio.create_family(
        actor_id="admin-a",
        tenant_id="tenant-a",
        **_draft("release-candidate"),
    )
    studio.submit(
        created["version_id"],
        actor_id="admin-a",
        tenant_id="tenant-a",
    )

    async def fake_candidate_eval(version):
        return {
            "ok": True,
            "candidate": {
                "version_id": version["version_id"],
                "content_hash": version["content_hash"],
                "domain": version["domain"],
                "ephemeral_runtime_skill_id": "skill-temp",
            },
            "isolation": {
                "temporary_runtime": True,
                "production_runtime_mutated": False,
                "production_skill_promoted": False,
            },
            "case_count": 2,
            "phase_count": 2,
            "failed_case_count": 0,
            "drift_case_count": 0,
            "phases": [],
            "comparisons": [],
            "failures": [],
        }

    monkeypatch.setattr(studio, "_run_candidate_evaluation", fake_candidate_eval)
    evaluated = asyncio.run(
        studio.evaluate(
            created["version_id"],
            actor_id="admin-a",
            tenant_id="tenant-a",
        )
    )
    evaluation_id = evaluated["evaluation"]["evaluation_id"]

    assert studio.get_evaluation(evaluation_id, tenant_id="tenant-a") is not None
    assert studio.get_evaluation(evaluation_id, tenant_id="tenant-b") is None
    before_export = studio.get_version(
        created["version_id"],
        tenant_id="tenant-a",
    )
    assert before_export is not None
    event_count = len(before_export["events"])

    first = studio.release_candidate(
        created["version_id"],
        tenant_id="tenant-a",
    )
    second = studio.release_candidate(
        created["version_id"],
        tenant_id="tenant-a",
    )
    assert first == second
    assert first["candidate_id"].startswith("studio-candidate-")
    assert len(first["export_hash"]) == 64
    assert first["source"]["content_hash"] == created["content_hash"]
    assert first["evaluation"]["ok"] is True
    assert first["evaluation"]["failed_case_count"] == 0
    assert first["evaluation"]["drift_case_count"] == 0
    assert all(value is False for value in first["authority"].values())

    with pytest.raises(KeyError):
        studio.release_candidate(
            created["version_id"],
            tenant_id="tenant-b",
        )

    after_export = studio.get_version(
        created["version_id"],
        tenant_id="tenant-a",
    )
    assert after_export is not None
    assert len(after_export["events"]) == event_count
    assert skills.snapshot() == runtime_before


def test_release_candidate_requires_evaluated_pass(tmp_path):
    studio, _ = _studio(tmp_path)
    created = studio.create_family(
        actor_id="admin-a",
        tenant_id="tenant-a",
        **_draft("draft-only"),
    )
    with pytest.raises(ValueError, match="evaluated_pass"):
        studio.release_candidate(
            created["version_id"],
            tenant_id="tenant-a",
        )
