from __future__ import annotations

import asyncio
from pathlib import Path

import ecomevo.product.skill_studio as studio_module
from ecomevo.evaluation import load_cases
from ecomevo.product.skill_studio import SkillStudioStore
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


def test_real_candidate_evaluation_uses_temp_runtime_and_preserves_production(tmp_path: Path, monkeypatch):
    production_skills = AdaptiveSkillLibrary(tmp_path / "production-runtime.db")
    studio = SkillStudioStore(tmp_path / "studio.db", production_skills, ToolRegistry())
    case = next(row for row in load_cases() if row["id"] == "aftersales_complete")
    monkeypatch.setattr(studio_module, "load_cases", lambda: [dict(case)])

    created = studio.create_family(
        actor_id="admin",
        domain="aftersales",
        name="售后证据核对候选",
        purpose="判责前核对订单、履约和用户举证",
        guidance="先核对订单号、物流履约与用户举证；证据完整时给出只读建议，高影响退款仍需人工确认。",
        preferred_tools=["order.inspect", "evidence.search"],
        trigger_terms=["售后", "退款", "订单", "破损"],
        input_contract={"requires": ["order_context"]},
        output_contract={"fields": ["recommendation"]},
        safety_notes="不得自动执行退款。",
        source_skill_id=None,
    )
    studio.submit(created["version_id"], actor_id="admin")
    production_before = production_skills.snapshot()

    evaluated = asyncio.run(studio.evaluate(created["version_id"], actor_id="admin"))
    assert evaluated["state"] == "evaluated_pass"
    assert evaluated["evaluation"]["case_count"] == 1
    assert evaluated["evaluation"]["failed_case_count"] == 0
    assert evaluated["evaluation"]["drift_case_count"] == 0
    assert production_skills.snapshot() == production_before

    snapshot = studio.get_evaluation(evaluated["evaluation"]["evaluation_id"])
    assert snapshot is not None
    result = snapshot["result"]
    assert result["candidate"]["content_hash"] == created["content_hash"]
    assert result["isolation"] == {
        "temporary_runtime": True,
        "production_runtime_mutated": False,
        "production_skill_promoted": False,
    }
    assert [phase["phase"] for phase in result["phases"]] == ["fresh", "persisted_replay"]
    assert result["comparisons"] == [{"id": "aftersales_complete", "stable": True}]
