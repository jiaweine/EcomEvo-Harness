import asyncio
import json
import sqlite3

from ecomevo.models import EvolutionPatch
from ecomevo.runtime import AdaptiveSkillLibrary, EcomEvoEngine, EventStore


class AdversarialReasoner:
    async def chat(self, *, messages, assets=None, temperature=0.0, max_tokens=1200):
        text = "\n".join(str(m.get("content", "")) for m in messages)
        if "提出可复用只读技能" in text:
            return json.dumps({
                "name": "证据补全技能",
                "guidance": "只使用只读工具补齐证据；资料不足时停止并请求补充。",
                "preferred_tools": ["evidence.search", "merchant.inspect"],
                "trigger_terms": ["主体标识"],
            }, ensure_ascii=False)
        if "只基于下面已经核对的工具结果" in text:
            return json.dumps({
                "summary": "已完成反证复核",
                "findings": ["仍以已核对证据为准"],
                "risks": [],
                "confidence": 0.72,
            }, ensure_ascii=False)
        return json.dumps({
            "objective": "补齐当前证据缺口",
            "tool_calls": [
                {"tool": "refund.issue", "purpose": "直接退款", "args": {}, "parallel_group": "unsafe"},
                {"tool": "evidence.search", "purpose": "寻找直接证据", "args": {"keywords": ["主体", "授权"]}, "parallel_group": "read"},
            ],
            "delegations": [{"role": "反证审查", "question": "检查证据缺口", "focus_tools": ["evidence.search"]}],
            "stop": False,
            "reflection": "继续只读核对",
        }, ensure_ascii=False)


class FakeGateway:
    def __init__(self, reasoner):
        self.reasoner = reasoner

    def current_provider(self):
        return self.reasoner


def _asset(tmp_path, content):
    path = tmp_path / "merchant.txt"
    path.write_text(content, encoding="utf-8")
    return {
        "id": "merchant",
        "name": "merchant.txt",
        "mime": "text/plain",
        "path": str(path),
        "meta": {"kind": "text", "text": content, "preview": content},
    }


def test_model_controller_is_autonomous_but_cannot_cross_side_effect_boundary(tmp_path):
    reasoner = AdversarialReasoner()
    engine = EcomEvoEngine(tmp_path / "runtime.db", model_gateway=FakeGateway(reasoner))
    summary = asyncio.run(engine.run("审核商家是否能直接通过", [], domain_hint="merchant_review"))

    assert summary.status == "needs_evidence"
    assert summary.proposed_actions == []
    assert summary.belief.facts["autonomy_mode"] == "model_controller"
    assert summary.autonomy_steps >= 1
    assert summary.delegations >= 1
    events = engine.events.list_events(summary.session_id)
    rejected = [
        item for event in events if event.event_type == "autonomy.decision_rejected"
        for item in event.payload.get("rejected", [])
    ]
    assert any("refund.issue" in item for item in rejected)
    assert any(event.event_type == "agent.delegated" for event in events)
    assert engine.events.verify_chain(summary.session_id)


def test_verified_case_still_only_proposes_confirmed_business_action(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    asset = _asset(tmp_path, "营业执照 91310000123456789A\n品牌授权书齐全\n处罚记录：无")
    summary = asyncio.run(engine.run("审核商家主体和品牌授权", [asset], domain_hint="merchant_review"))

    assert summary.status == "completed"
    assert summary.proposed_actions
    assert all(action.requires_confirmation for action in summary.proposed_actions if action.side_effect)
    assert summary.belief.facts["autonomy_mode"] == "deterministic_fallback"


def test_skill_library_persists_posterior_and_quality_diversity_archive(tmp_path):
    db = tmp_path / "skills.db"
    skills = AdaptiveSkillLibrary(db)
    first = skills.upsert_candidate(
        domain="merchant_review",
        name="主体补证",
        guidance="优先核验主体标识",
        preferred_tools=["merchant.inspect", "evidence.search"],
        trigger_terms=["主体标识"],
        shadow_score=1.0,
        promote=True,
    )
    assert first.status == "active"
    before = first.posterior_mean
    updated = skills.record_outcome([first.skill_id], success=False, score=.3, session_id="s1")[0]
    assert updated.posterior_mean < before

    restored = AdaptiveSkillLibrary(db)
    same = restored.get(first.skill_id)
    assert same is not None and same.uses == 1 and same.losses == 1
    assert restored.relevant("merchant_review", query="主体标识")


def test_event_store_semantically_deduplicates_concurrent_evolution_candidates(tmp_path):
    store = EventStore(tmp_path / "events.db")
    patch = EvolutionPatch(
        patch_id="patch-a", created_at=1.0, reason="x", target="planner",
        patch={"domain": "merchant_review", "add_required_checks": ["主体标识"]},
        replay_cases=10, regression_before=.5, regression_after=1.0, accepted=True,
    )
    assert store.save_patch_if_novel(patch) is None
    duplicate = patch.model_copy(update={"patch_id": "patch-b", "created_at": 2.0})
    existing = store.save_patch_if_novel(duplicate)
    assert existing is not None and existing["patch_id"] == "patch-a"
    assert len(store.list_patches(10)) == 1


def test_event_store_migrates_legacy_evolution_table_without_losing_patches(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE sessions(session_id TEXT PRIMARY KEY,parent_session_id TEXT,parent_seq INTEGER,created_at REAL NOT NULL,meta_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE events(session_id TEXT NOT NULL,seq INTEGER NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,ts REAL NOT NULL,prev_hash TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(session_id,seq));
        CREATE TABLE snapshots(session_id TEXT NOT NULL,seq INTEGER NOT NULL,snapshot_blob TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,seq));
        CREATE TABLE evolution_patches(patch_id TEXT PRIMARY KEY,created_at REAL NOT NULL,payload_json TEXT NOT NULL);
        """)
        payload = json.dumps({
            "patch_id": "old", "created_at": 1.0, "reason": "legacy", "target": "planner",
            "patch": {"domain": "merchant_review", "add_required_checks": ["主体标识"]},
            "replay_cases": 8, "regression_before": .5, "regression_after": 1.0, "accepted": True,
        }, ensure_ascii=False)
        c.execute("INSERT INTO evolution_patches VALUES(?,?,?)", ("old", 1.0, payload))

    store = EventStore(db)
    assert store.list_patches(10)[0]["patch_id"] == "old"
    with sqlite3.connect(db) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(evolution_patches)")}
    assert "fingerprint" in columns
