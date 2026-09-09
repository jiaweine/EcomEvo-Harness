from __future__ import annotations

import asyncio

from ecomevo.runtime.grouped_restore_event_store import (
    GroupedRestoreBundledEventStore,
    _GroupedRestore,
    _LoopRestoreGroup,
)


class RecordingRestoreStore(GroupedRestoreBundledEventStore):
    def __init__(self, path):
        self.read_batches: list[int] = []
        super().__init__(path)

    def _restore_checkpoint_group(self, batch):
        self.read_batches.append(len(batch))
        return [None] * len(batch)


def _request(loop, *, cancelled: bool):
    future = loop.create_future()
    if cancelled:
        future.cancel()
    return _GroupedRestore("missing", 1, future)


def test_cancelled_restore_queue_does_not_start_sqlite_reads(tmp_path):
    async def exercise():
        store = RecordingRestoreStore(tmp_path / "cancelled-restores.db")
        loop = asyncio.get_running_loop()
        group = _LoopRestoreGroup(scheduled=True)
        group.queue.extend(_request(loop, cancelled=True) for _ in range(130))

        await store._flush_restore_group(group)

        assert store.read_batches == []
        assert group.queue == []
        assert not group.scheduled
        assert group.worker is None

    asyncio.run(exercise())


def test_cancelled_restore_requests_are_removed_from_mixed_batch(tmp_path):
    async def exercise():
        store = RecordingRestoreStore(tmp_path / "mixed-restores.db")
        loop = asyncio.get_running_loop()
        group = _LoopRestoreGroup(scheduled=True)
        group.queue.extend(_request(loop, cancelled=True) for _ in range(63))
        active = _request(loop, cancelled=False)
        group.queue.append(active)

        await store._flush_restore_group(group)

        assert store.read_batches == [1]
        assert active.future.result() is None

    asyncio.run(exercise())
