from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ecomevo.product.skill_studio import SkillStudioStore
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


def _studio(tmp_path: Path):
    skills = AdaptiveSkillLibrary(tmp_path / "runtime.db")
    tools = ToolRegistry()
    studio = SkillStudioStore(tmp_path / "studio.db", skills, tools)
    return studio, skills


def _draft(**overrides):
    value = {
        "domain": "aftersales",
        "name": "售后证据补全",
        "purpose": "在判责前确认订单、履约和用户举证是否齐备",
        "guidance": "先核对订单与履约事实，再检查用户举证；证据不足时停止并请求补证，不得直接执行退款。",
        "preferred_tools": ["order.inspect", "evidence.search"],
        "trigger_terms": ["退款", "未收到货", "签收"],
        "input_contract": {"requires": ["order_context"]},
        "output_contract": {"fields": ["evidence_gaps", "recommendation"]},
        "safety_notes": "不得绕过审批或把 guidance 当作事实证据。",
        "source_skill_id": None,
    }
    value.update(overrides)
    return value


def test_versions_are_immutable_and_do_not_mutate_runtime_skills(tmp_path):
    studio, skills = _studio(tmp_path)
    before = skills.snapshot()

    first = studio.create_family(actor_id="admin-a", **_draft())
    assert first["version"] == 1
    assert first["state"] == "draft"
    assert first["authority"]["can_promote_runtime"] is False

    second = studio.create_version(
        first["family_id"],
        actor_id="admin-a",
        **_draft(guidance="先核对订单、签收、物流轨迹和用户举证；缺少任一关键事实时停止并补证，不得直接执行退款。"),
    )
    assert second["version"] == 2
    assert second["content_hash"] != first["content_hash"]
    assert studio.get_version(first["version_id"])["guidance"] == first["guidance"]
    assert skills.snapshot() == before


def test_preferred_tools_must_exist_in_registry(tmp_path):
    studio, _ = _studio(tmp_path)
    with pytest.raises(ValueError, match="unknown preferred tools"):
        studio.create_family(actor_id="admin-a", **_draft(preferred_tools=["refund.execute"]))


def test_source_runtime_skill_must_exist(tmp_path):
    studio, skills = _studio(tmp_path)
    with pytest.raises(ValueError, match="source runtime skill"):
        studio.create_family(actor_id="admin-a", **_draft(source_skill_id="skill-does-not-exist"))

    source = skills.upsert_candidate(
        domain="aftersales",
        name="runtime source",
        guidance="核对订单与证据后给出只读建议。",
        preferred_tools=["order.inspect"],
        trigger_terms=["退款"],
        shadow_score=0.8,
    )
    created = studio.create_family(actor_id="admin-a", **_draft(source_skill_id=source.skill_id))
    assert created["source_skill_id"] == source.skill_id


def test_candidate_evaluation_snapshot_is_append_only_and_never_promotes_runtime(tmp_path, monkeypatch):
    studio, skills = _studio(tmp_path)
    runtime_before = skills.snapshot()
    created = studio.create_family(actor_id="admin-a", **_draft())
    submitted = studio.submit(created["version_id"], actor_id="reviewer-a", note="进入评估")
    assert submitted["state"] == "review"

    async def fake_candidate_eval(version):
        assert version["content_hash"] == created["content_hash"]
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
    evaluated = asyncio.run(studio.evaluate(created["version_id"], actor_id="reviewer-a"))
    assert evaluated["state"] == "evaluated_pass"
    assert evaluated["evaluation"]["isolated_runtime"] is True
    assert [event["event_type"] for event in evaluated["events"]] == [
        "created",
        "submitted",
        "candidate_evaluated",
    ]
    evaluation_id = evaluated["evaluation"]["evaluation_id"]
    snapshot = studio.get_evaluation(evaluation_id)
    assert snapshot["content_hash"] == created["content_hash"]
    assert snapshot["result"]["isolation"] == {
        "temporary_runtime": True,
        "production_runtime_mutated": False,
        "production_skill_promoted": False,
    }
    assert all(value is False for value in snapshot["authority"].values())
    assert skills.snapshot() == runtime_before


def test_candidate_evaluation_requires_review_state(tmp_path):
    studio, _ = _studio(tmp_path)
    created = studio.create_family(actor_id="admin-a", **_draft())
    with pytest.raises(ValueError, match="submit the version"):
        asyncio.run(studio.evaluate(created["version_id"], actor_id="admin-a"))


def test_archive_is_terminal_even_if_old_evaluation_exists(tmp_path, monkeypatch):
    studio, _ = _studio(tmp_path)
    created = studio.create_family(actor_id="admin-a", **_draft())
    studio.submit(created["version_id"], actor_id="admin-a")

    async def fake_candidate_eval(version):
        return {
            "ok": False,
            "candidate": {"version_id": version["version_id"], "content_hash": version["content_hash"], "domain": version["domain"], "ephemeral_runtime_skill_id": "skill-temp"},
            "isolation": {"temporary_runtime": True, "production_runtime_mutated": False, "production_skill_promoted": False},
            "case_count": 1,
            "phase_count": 2,
            "failed_case_count": 1,
            "drift_case_count": 0,
            "phases": [],
            "comparisons": [],
            "failures": ["case-x"],
        }

    monkeypatch.setattr(studio, "_run_candidate_evaluation", fake_candidate_eval)
    failed = asyncio.run(studio.evaluate(created["version_id"], actor_id="admin-a"))
    assert failed["state"] == "evaluated_fail"
    archived = studio.archive(created["version_id"], actor_id="admin-a", note="superseded")
    assert archived["state"] == "archived"
    assert archived["events"][-1]["event_type"] == "archived"


def test_submit_requires_draft(tmp_path):
    studio, _ = _studio(tmp_path)
    created = studio.create_family(actor_id="admin-a", **_draft())
    studio.submit(created["version_id"], actor_id="admin-a")
    with pytest.raises(ValueError, match="only draft"):
        studio.submit(created["version_id"], actor_id="admin-a")
