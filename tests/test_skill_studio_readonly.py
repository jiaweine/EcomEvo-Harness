from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ecomevo.evaluation import EvaluationCenter
from ecomevo.product.skill_studio import SkillStudioStore
from ecomevo.runtime.skills import AdaptiveSkillLibrary
from ecomevo.runtime.tools import ToolRegistry


def _draft(name: str):
    return {
        "domain": "aftersales",
        "name": name,
        "purpose": "检查售后证据链",
        "guidance": "先核对订单和履约事实，证据缺失时停止并请求补证，不得直接执行退款动作。",
        "preferred_tools": ["order.inspect"],
        "trigger_terms": ["退款", "订单"],
        "input_contract": {},
        "output_contract": {},
        "safety_notes": "保持确定性审批边界。",
        "source_skill_id": None,
    }


def _studio(tmp_path: Path):
    runtime_path = tmp_path / "runtime.db"
    skills = AdaptiveSkillLibrary(runtime_path)
    studio = SkillStudioStore(
        tmp_path / "studio.db",
        skills,
        ToolRegistry(),
        EvaluationCenter(tmp_path / "evaluation.db"),
    )
    return studio, runtime_path


def _policy_count(runtime_path: Path) -> int:
    with sqlite3.connect(runtime_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM evolution_policy").fetchone()[0])


def test_catalog_does_not_bootstrap_or_mutate_runtime_policy(tmp_path):
    studio, runtime_path = _studio(tmp_path)
    assert _policy_count(runtime_path) == 0
    first = studio.catalog()
    second = studio.catalog()
    assert first["evolution_policies"] == []
    assert second["evolution_policies"] == []
    assert _policy_count(runtime_path) == 0


def test_catalog_reads_existing_runtime_policy_without_changing_it(tmp_path):
    studio, runtime_path = _studio(tmp_path)
    with sqlite3.connect(runtime_path) as connection:
        connection.execute(
            "INSERT INTO evolution_policy(domain,promotion_threshold,retirement_threshold,exploration,updates,updated_at) VALUES(?,?,?,?,?,?)",
            ("aftersales", 0.93, 0.44, 0.51, 7, 123.0),
        )
    before = _policy_count(runtime_path)
    rows = studio.catalog()["evolution_policies"]
    assert rows == [{
        "domain": "aftersales",
        "promotion_threshold": 0.93,
        "retirement_threshold": 0.44,
        "exploration": 0.51,
        "updates": 7,
        "updated_at": 123.0,
    }]
    assert _policy_count(runtime_path) == before


def test_concurrent_version_creation_allocates_distinct_monotonic_versions(tmp_path):
    studio, _ = _studio(tmp_path)
    first = studio.create_family(actor_id="admin", **_draft("base"))

    def create(index: int):
        return studio.create_version(
            first["family_id"],
            actor_id=f"admin-{index}",
            **_draft(f"revision-{index}"),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(create, range(4)))
    versions = sorted(item["version"] for item in created)
    assert versions == [2, 3, 4, 5]
    assert len({item["version_id"] for item in created}) == 4
