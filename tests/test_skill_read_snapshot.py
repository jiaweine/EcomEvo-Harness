from __future__ import annotations

import asyncio
import threading

from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary
from ecomevo.runtime.skills import AdaptiveSkillLibrary


DOMAIN = "merchant_review"
OTHER_DOMAIN = "catalog_review"


class CountingBundledSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path):
        self.connections = 0
        self._connection_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        with self._connection_lock:
            self.connections += 1
        return super()._conn()


def seed_active_skills(path, domain: str = DOMAIN):
    base = AdaptiveSkillLibrary(path)
    base.policy(domain)
    skills = []
    for index, (score, term, tool) in enumerate(
        [
            (0.94, "授权", "merchant.inspect"),
            (0.86, "风险", "risk.scan"),
            (0.78, "主体", "evidence.search"),
        ]
    ):
        skills.append(
            base.upsert_candidate(
                domain=domain,
                name=f"skill-{index}",
                guidance=f"guidance-{index}",
                preferred_tools=[tool],
                trigger_terms=[term],
                shadow_score=score,
                promote=True,
            )
        )
    return base, skills


def test_fused_relevant_and_policy_match_base_and_use_one_connection(tmp_path):
    path = tmp_path / "skills.db"
    base, _ = seed_active_skills(path)
    expected_skills = base.relevant(
        DOMAIN,
        query="审核商家主体与授权并检查历史风险",
        missing=["主体", "授权", "风险"],
    )
    expected_policy = base.policy(DOMAIN)

    bundled = CountingBundledSkills(path)
    bundled.connections = 0
    actual_skills = bundled.relevant(
        DOMAIN,
        query="审核商家主体与授权并检查历史风险",
        missing=["主体", "授权", "风险"],
    )
    actual_policy = bundled.policy(DOMAIN)

    assert [skill.skill_id for skill in actual_skills] == [
        skill.skill_id for skill in expected_skills
    ]
    assert actual_policy == expected_policy
    assert bundled.connections == 1


def test_fused_policy_snapshot_is_one_shot(tmp_path):
    path = tmp_path / "one-shot.db"
    seed_active_skills(path)
    bundled = CountingBundledSkills(path)
    bundled.connections = 0

    bundled.relevant(DOMAIN, query="授权", missing=["风险"])
    first = bundled.policy(DOMAIN)
    assert bundled.connections == 1

    second = bundled.policy(DOMAIN)
    assert second == first
    assert bundled.connections == 2


def test_mismatched_policy_lookup_discards_fused_snapshot(tmp_path):
    path = tmp_path / "mismatch.db"
    base, _ = seed_active_skills(path)
    base.policy(OTHER_DOMAIN)
    bundled = CountingBundledSkills(path)
    bundled.connections = 0

    bundled.relevant(DOMAIN, query="授权")
    assert bundled.connections == 1
    bundled.policy(OTHER_DOMAIN)
    assert bundled.connections == 2
    bundled.policy(DOMAIN)
    assert bundled.connections == 3


def test_async_policy_write_invalidates_snapshot_in_caller_context(tmp_path):
    async def exercise():
        path = tmp_path / "async-write.db"
        seed_active_skills(path)
        bundled = CountingBundledSkills(path)
        bundled.connections = 0

        bundled.relevant(DOMAIN, query="授权")
        prepared = bundled._decision_policy_snapshot.get()
        assert prepared is not None
        stale_updates = prepared[1]["updates"]
        assert bundled.connections == 1

        await bundled.note_run_async(DOMAIN, success=False, skill_used=False)
        assert bundled.connections == 2
        fresh = bundled.policy(DOMAIN)
        assert bundled.connections == 3
        assert fresh["updates"] == stale_updates + 1

    asyncio.run(exercise())


def test_record_outcome_policy_write_invalidates_snapshot(tmp_path):
    path = tmp_path / "outcome-write.db"
    _, seeded = seed_active_skills(path)
    bundled = CountingBundledSkills(path)
    bundled.connections = 0

    bundled.relevant(DOMAIN, query="授权")
    prepared = bundled._decision_policy_snapshot.get()
    assert prepared is not None
    stale_updates = prepared[1]["updates"]
    assert bundled.connections == 1

    bundled.record_outcome(
        [seeded[0].skill_id],
        success=False,
        score=0.2,
        session_id="snapshot-test",
    )
    assert bundled.connections == 2
    fresh = bundled.policy(DOMAIN)
    assert bundled.connections == 3
    assert fresh["updates"] == stale_updates + 1


def test_fused_snapshot_is_context_local_across_async_tasks(tmp_path):
    async def exercise():
        path = tmp_path / "context.db"
        base, _ = seed_active_skills(path, DOMAIN)
        seed_active_skills(path, OTHER_DOMAIN)
        # Give the second domain a visibly different policy value before the read snapshot.
        base.note_run(OTHER_DOMAIN, success=False, skill_used=False)
        expected = {
            DOMAIN: base.policy(DOMAIN),
            OTHER_DOMAIN: base.policy(OTHER_DOMAIN),
        }

        bundled = CountingBundledSkills(path)
        bundled.connections = 0
        ready = 0
        ready_lock = asyncio.Lock()
        both_ready = asyncio.Event()

        async def one(domain: str):
            nonlocal ready
            bundled.relevant(domain, query="授权 风险")
            async with ready_lock:
                ready += 1
                if ready == 2:
                    both_ready.set()
            await both_ready.wait()
            return bundled.policy(domain)

        first, second = await asyncio.gather(one(DOMAIN), one(OTHER_DOMAIN))
        assert first == expected[DOMAIN]
        assert second == expected[OTHER_DOMAIN]
        assert bundled.connections == 2

    asyncio.run(exercise())
