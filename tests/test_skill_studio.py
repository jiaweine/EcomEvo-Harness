from __future__ import annotations

from pathlib import Path

import pytest

from ecomevo.evaluation import EvaluationCenter
from ecomevo.product.skill_studio import SkillStudioStore
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


def _studio(tmp_path: Path):
    skills = AdaptiveSkillLibrary(tmp_path / "runtime.db")
    tools = ToolRegistry()
    evaluation = EvaluationCenter(tmp_path / "evaluation.db")
    studio = SkillStudioStore(tmp_path / "studio.db", skills, tools, evaluation)
    return studio, skills, evaluation


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


def _evaluation_result(*, ok: bool = True):
    return {
        "ok": ok,
        "case_count": 2,
        "phase_count": 2,
        "source_hash": "gold-hash",
        "fixture_snapshot": [],
        "phases": [],
        "comparisons": [],
        "summary": {
            "case_count": 2,
            "passed_case_count": 2 if ok else 1,
            "failed_case_count": 0 if ok else 1,
            "drift_case_count": 0,
            "phase_count": 2,
            "domains": {"aftersales": 2},
        },
        "failures": [] if ok else ["case-x"],
    }


def test_versions_are_immutable_and_do_not_mutate_runtime_skills(tmp_path):
    studio, skills, _ = _studio(tmp_path)
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
    studio, _, _ = _studio(tmp_path)
    with pytest.raises(ValueError, match="unknown preferred tools"):
        studio.create_family(actor_id="admin-a", **_draft(preferred_tools=["refund.execute"]))


def test_source_runtime_skill_must_exist(tmp_path):
    studio, skills, _ = _studio(tmp_path)
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


def test_review_and_evaluation_are_append_only_and_never_promote_runtime(tmp_path):
    studio, skills, evaluation = _studio(tmp_path)
    runtime_before = skills.snapshot()
    created = studio.create_family(actor_id="admin-a", **_draft())
    submitted = studio.submit(created["version_id"], actor_id="reviewer-a", note="进入评估")
    assert submitted["state"] == "review"

    run = evaluation.store.record(_evaluation_result(ok=True))
    linked = studio.link_evaluation(created["version_id"], run["id"], actor_id="reviewer-a")
    assert linked["state"] == "evaluated_pass"
    assert linked["evaluation"]["run_id"] == run["id"]
    assert linked["evaluation"]["ok"] is True
    assert [event["event_type"] for event in linked["events"]] == [
        "created",
        "submitted",
        "evaluation_linked",
    ]
    assert all(value is False for value in linked["authority"].values())
    assert skills.snapshot() == runtime_before


def test_failed_evaluation_and_archive_state(tmp_path):
    studio, _, evaluation = _studio(tmp_path)
    created = studio.create_family(actor_id="admin-a", **_draft())
    studio.submit(created["version_id"], actor_id="admin-a")
    run = evaluation.store.record(_evaluation_result(ok=False))
    failed = studio.link_evaluation(created["version_id"], run["id"], actor_id="admin-a")
    assert failed["state"] == "evaluated_fail"
    archived = studio.archive(created["version_id"], actor_id="admin-a", note="superseded")
    assert archived["state"] == "archived"
    assert archived["events"][-1]["event_type"] == "archived"


def test_submit_requires_draft_and_link_requires_existing_evaluation(tmp_path):
    studio, _, _ = _studio(tmp_path)
    created = studio.create_family(actor_id="admin-a", **_draft())
    studio.submit(created["version_id"], actor_id="admin-a")
    with pytest.raises(ValueError, match="only draft"):
        studio.submit(created["version_id"], actor_id="admin-a")
    with pytest.raises(ValueError, match="evaluation run does not exist"):
        studio.link_evaluation(created["version_id"], "eval-missing", actor_id="admin-a")
