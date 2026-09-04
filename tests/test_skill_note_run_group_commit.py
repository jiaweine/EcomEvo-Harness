from __future__ import annotations

import asyncio
import threading

import pytest

from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary


DOMAIN = "merchant_review"


class TracingSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path):
        self.immediate_begins = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)
        self.immediate_begins = 0

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._trace_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection


async def _successful_notes(skills: BundledAdaptiveSkillLibrary, count: int) -> None:
    await asyncio.gather(
        *(
            skills.note_run_async(DOMAIN, success=True, skill_used=False)
            for _ in range(count)
        )
    )


def test_successful_no_skill_notes_share_one_writer_transaction(tmp_path):
    skills = TracingSkills(tmp_path / "skills.db")

    async def run():
        before = skills.policy(DOMAIN)
        skills.immediate_begins = 0
        await _successful_notes(skills, 32)
        return before, skills.policy(DOMAIN)

    before, after = asyncio.run(run())

    assert skills.immediate_begins == 1
    assert after["updates"] == before["updates"] + 32
    assert after["promotion_threshold"] == before["promotion_threshold"]
    assert after["retirement_threshold"] == before["retirement_threshold"]
    assert after["exploration"] == before["exploration"]


def test_note_run_group_commit_bounds_large_batches_at_64(tmp_path):
    skills = TracingSkills(tmp_path / "skills.db")

    async def run():
        before = skills.policy(DOMAIN)["updates"]
        skills.immediate_begins = 0
        await _successful_notes(skills, 130)
        return before, skills.policy(DOMAIN)["updates"]

    before, after = asyncio.run(run())

    assert skills.immediate_begins == 3
    assert after == before + 130


def test_learning_note_is_a_group_commit_barrier(tmp_path):
    skills = TracingSkills(tmp_path / "skills.db")

    async def run():
        before = skills.policy(DOMAIN)
        skills.immediate_begins = 0
        calls = [
            *(
                skills.note_run_async(DOMAIN, success=True, skill_used=False)
                for _ in range(8)
            ),
            skills.note_run_async(DOMAIN, success=False, skill_used=False),
            *(
                skills.note_run_async(DOMAIN, success=True, skill_used=False)
                for _ in range(8)
            ),
        ]
        await asyncio.gather(*calls)
        return before, skills.policy(DOMAIN)

    before, after = asyncio.run(run())

    # safe-prefix batch + one learning transaction + safe-suffix batch
    assert skills.immediate_begins == 3
    assert after["updates"] == before["updates"] + 17
    assert after["exploration"] == min(0.90, before["exploration"] + 0.025)
    assert after["promotion_threshold"] == before["promotion_threshold"]
    assert after["retirement_threshold"] == before["retirement_threshold"]


class BlockingGroupedSkills(TracingSkills):
    def __init__(self, path):
        self.persist_started = threading.Event()
        self.persist_release = threading.Event()
        self.block = False
        super().__init__(path)

    def _persist_note_run_group(self, batch):
        if self.block:
            self.persist_started.set()
            if not self.persist_release.wait(timeout=3):
                raise TimeoutError("test did not release note-run persistence")
        return super()._persist_note_run_group(batch)


def test_queued_note_cancellation_before_persistence_drops_the_write(tmp_path):
    skills = BlockingGroupedSkills(tmp_path / "skills.db")

    async def run():
        before = skills.policy(DOMAIN)["updates"]
        skills.block = True
        skills.persist_started.clear()
        skills.persist_release.clear()

        first = asyncio.create_task(
            skills.note_run_async(DOMAIN, success=True, skill_used=False)
        )
        started = await asyncio.to_thread(skills.persist_started.wait, 1)
        assert started is True

        second = asyncio.create_task(
            skills.note_run_async(DOMAIN, success=True, skill_used=False)
        )
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(second, timeout=0.2)

        skills.persist_release.set()
        await first
        return before, skills.policy(DOMAIN)["updates"]

    before, after = asyncio.run(run())
    assert after == before + 1


def test_started_note_cancellation_waits_for_durable_update(tmp_path):
    skills = BlockingGroupedSkills(tmp_path / "skills.db")

    async def run():
        before = skills.policy(DOMAIN)["updates"]
        skills.block = True
        skills.persist_started.clear()
        skills.persist_release.clear()

        task = asyncio.create_task(
            skills.note_run_async(DOMAIN, success=True, skill_used=False)
        )
        started = await asyncio.to_thread(skills.persist_started.wait, 1)
        assert started is True

        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        skills.persist_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return before, skills.policy(DOMAIN)["updates"]

    before, after = asyncio.run(run())
    assert after == before + 1


class FailSharedSkills(TracingSkills):
    def __init__(self, path):
        self.fail_next_shared = False
        super().__init__(path)

    def _persist_note_run_group(self, batch):
        if self.fail_next_shared and len(batch) > 1:
            self.fail_next_shared = False
            raise RuntimeError("synthetic shared note-run failure")
        return super()._persist_note_run_group(batch)


def test_failed_shared_note_batch_isolates_valid_requests(tmp_path):
    skills = FailSharedSkills(tmp_path / "skills.db")

    async def run():
        before = skills.policy(DOMAIN)["updates"]
        skills.fail_next_shared = True
        await _successful_notes(skills, 8)
        return before, skills.policy(DOMAIN)["updates"]

    before, after = asyncio.run(run())
    assert after == before + 8
