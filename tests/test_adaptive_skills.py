from __future__ import annotations

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
