import asyncio
import threading

import pytest

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer


def _catalog():
    return [
        {
            "tool": "merchant.inspect",
            "mode": "read-only",
            "purpose": "read merchant identity and authorization evidence",
            "evidence_tags": ["merchant_identity", "authorization"],
            "cost": 1.0,
        },
        {
            "tool": "refund.execute",
            "mode": "write",
            "purpose": "change business state",
            "evidence_tags": ["merchant_identity"],
            "cost": 1.0,
        },
    ]


def _trajectory(index: int):
    return {
        "goal": f"review merchant identity and authorization replay {index}",
        "missing": ["merchant identity", "authorization evidence"],
    }


class TracingReplayHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.immediate_begins = 0
        super().__init__(path)
        self.immediate_begins = 0

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str) -> None:
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection

    def replay_count(self, domain: str = "merchant_review") -> int:
        with self._conn() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM harness_replay_cases WHERE domain=?",
                    (domain,),
                ).fetchone()[0]
            )


async def _seed_shadow(optimizer: BundledHarnessEvolutionOptimizer) -> None:
    candidate = await optimizer.propose(
        "merchant_review",
        trajectory=_trajectory(-1),
        tool_catalog=_catalog(),
        reasoner=None,
    )
    assert candidate is not None
    assert candidate["status"] == "shadow"


async def _propose_existing_shadow(
    optimizer: BundledHarnessEvolutionOptimizer,
    count: int,
):
    return await asyncio.gather(
        *(
            optimizer.propose(
                "merchant_review",
                trajectory=_trajectory(index),
                tool_catalog=_catalog(),
                reasoner=None,
            )
            for index in range(count)
        )
    )


def test_phase_aligned_replay_evidence_shares_one_writer_transaction(tmp_path):
    optimizer = TracingReplayHarness(tmp_path / "runtime.db")

    async def run():
        await _seed_shadow(optimizer)
        optimizer.immediate_begins = 0
        before = optimizer.replay_count()
        results = await _propose_existing_shadow(optimizer, 32)
        return before, results

    before, results = asyncio.run(run())

    assert results == [None] * 32
    assert optimizer.replay_count() == before + 32
    assert optimizer.immediate_begins == 1


def test_replay_group_commit_bounds_large_batches_at_64(tmp_path):
    optimizer = TracingReplayHarness(tmp_path / "runtime.db")

    async def run():
        await _seed_shadow(optimizer)
        optimizer.immediate_begins = 0
        before = optimizer.replay_count()
        results = await _propose_existing_shadow(optimizer, 130)
        return before, results

    before, results = asyncio.run(run())

    assert results == [None] * 130
    assert optimizer.replay_count() == before + 130
    assert optimizer.immediate_begins == 3


class BlockingReplayHarness(TracingReplayHarness):
    def __init__(self, path):
        self.block_replay = False
        self.persist_started = threading.Event()
        self.persist_release = threading.Event()
        super().__init__(path)

    def _persist_replay_group(self, batch):
        if self.block_replay:
            self.persist_started.set()
            if not self.persist_release.wait(timeout=3):
                raise TimeoutError("test did not release replay persistence")
        return super()._persist_replay_group(batch)


def test_grouped_replay_sqlite_work_stays_off_event_loop(tmp_path):
    optimizer = BlockingReplayHarness(tmp_path / "runtime.db")

    async def run():
        await _seed_shadow(optimizer)
        optimizer.block_replay = True
        optimizer.persist_started.clear()
        optimizer.persist_release.clear()

        proposals = asyncio.create_task(_propose_existing_shadow(optimizer, 16))
        started = await asyncio.to_thread(optimizer.persist_started.wait, 1)
        assert started is True

        # The worker is deliberately blocked in synchronous SQLite-side persistence.
        # Reaching this scheduler turn proves that work is not occupying the event loop.
        await asyncio.sleep(0)
        assert not proposals.done()

        optimizer.persist_release.set()
        results = await proposals
        assert results == [None] * 16

    asyncio.run(run())


def test_replay_caller_cancellation_waits_for_durable_evidence(tmp_path):
    optimizer = BlockingReplayHarness(tmp_path / "runtime.db")

    async def run():
        await _seed_shadow(optimizer)
        before = optimizer.replay_count()
        optimizer.block_replay = True
        optimizer.persist_started.clear()
        optimizer.persist_release.clear()

        task = asyncio.create_task(
            optimizer.propose(
                "merchant_review",
                trajectory=_trajectory(99),
                tool_catalog=_catalog(),
                reasoner=None,
            )
        )
        started = await asyncio.to_thread(optimizer.persist_started.wait, 1)
        assert started is True

        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        optimizer.persist_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert optimizer.replay_count() == before + 1

    asyncio.run(run())


class FailSharedReplayHarness(TracingReplayHarness):
    def __init__(self, path):
        self.fail_next_shared = False
        super().__init__(path)

    def _persist_replay_group(self, batch):
        if self.fail_next_shared and len(batch) > 1:
            self.fail_next_shared = False
            raise RuntimeError("synthetic shared replay failure")
        return super()._persist_replay_group(batch)


def test_failed_shared_replay_batch_isolates_valid_requests(tmp_path):
    optimizer = FailSharedReplayHarness(tmp_path / "runtime.db")

    async def run():
        await _seed_shadow(optimizer)
        before = optimizer.replay_count()
        optimizer.fail_next_shared = True
        results = await _propose_existing_shadow(optimizer, 8)
        return before, results

    before, results = asyncio.run(run())

    assert results == [None] * 8
    assert optimizer.replay_count() == before + 8
