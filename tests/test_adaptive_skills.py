from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from ecomevo.runtime.skills import AdaptiveSkillLibrary


class CoordinatedReadLibrary(AdaptiveSkillLibrary):
    """Force every legacy read/compute/write policy update to read the same row."""

    def __init__(self, db_path, barrier: threading.Barrier):
        self._read_barrier = barrier
        super().__init__(db_path)

    def policy(self, domain: str):
        value = super().policy(domain)
        self._read_barrier.wait(timeout=3)
        return value


def test_policy_adaptation_is_atomic_across_library_instances(tmp_path):
    workers = 8
    db = tmp_path / "skills.db"
    barrier = threading.Barrier(workers)
    libraries = [CoordinatedReadLibrary(db, barrier) for _ in range(workers)]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(library.note_run, "merchant_review", success=False, skill_used=False)
            for library in libraries
        ]
        for future in futures:
            future.result(timeout=5)

    policy = AdaptiveSkillLibrary(db).policy("merchant_review")
    assert policy["updates"] == workers
    assert policy["exploration"] == pytest.approx(0.60 + workers * 0.025)


def test_multi_skill_outcome_is_one_atomic_learning_round(tmp_path):
    db = tmp_path / "skills-batch.db"
    library = AdaptiveSkillLibrary(db)
    first = library.upsert_candidate(
        domain="merchant_review",
        name="主体核对",
        guidance="优先核对主体资料",
        preferred_tools=["merchant.inspect"],
        trigger_terms=["主体"],
        shadow_score=0.96,
        promote=True,
    )
    second = library.upsert_candidate(
        domain="merchant_review",
        name="授权核对",
        guidance="优先核对授权资料",
        preferred_tools=["evidence.search"],
        trigger_terms=["授权"],
        shadow_score=0.95,
        promote=True,
    )
    before_first = library.get(first.skill_id)
    before_second = library.get(second.skill_id)
    before_policy = library.policy("merchant_review")
    assert before_first is not None and before_second is not None

    # Force the second outcome insert to abort. A batched learning round must roll
    # back the first skill update too; the legacy per-skill transaction path would
    # leave a partially learned round behind.
    with sqlite3.connect(db) as connection:
        connection.execute(
            f"""CREATE TRIGGER abort_second_skill
                BEFORE INSERT ON skill_outcomes
                WHEN NEW.skill_id='{second.skill_id}'
                BEGIN
                    SELECT RAISE(ABORT, 'forced batch rollback');
                END;"""  # nosec - test fixture uses a generated local identifier
        )

    with pytest.raises(sqlite3.IntegrityError):
        library.record_outcome(
            [first.skill_id, second.skill_id],
            success=True,
            score=0.91,
            session_id="rollback-round",
        )

    after_failure_first = library.get(first.skill_id)
    after_failure_second = library.get(second.skill_id)
    after_failure_policy = library.policy("merchant_review")
    assert after_failure_first is not None and after_failure_second is not None
    assert after_failure_first.uses == before_first.uses
    assert after_failure_second.uses == before_second.uses
    assert after_failure_policy["updates"] == before_policy["updates"]
    with sqlite3.connect(db) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM skill_outcomes WHERE session_id='rollback-round'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER abort_second_skill")
    assert count == 0

    updated = library.record_outcome(
        [first.skill_id, second.skill_id],
        success=True,
        score=0.91,
        session_id="committed-round",
    )
    assert [row.skill_id for row in updated] == [first.skill_id, second.skill_id]
    assert all(row.uses == 1 for row in updated)
    after_policy = library.policy("merchant_review")
    assert after_policy["updates"] == before_policy["updates"] + 1
    with sqlite3.connect(db) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM skill_outcomes WHERE session_id='committed-round'"
        ).fetchone()[0]
    assert count == 2
