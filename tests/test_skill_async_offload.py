from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ecomevo.runtime.bundled_skills import BundledAdaptiveSkillLibrary
from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.skills import AdaptiveSkillLibrary


DOMAIN = "merchant_review"


class TracingSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path):
        self.immediate_begins = 0
        self._trace_lock = threading.Lock()
        super().__init__(path)

    def _conn(self):
        connection = super()._conn()

        def trace(statement: str):
            if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
                with self._trace_lock:
                    self.immediate_begins += 1

        connection.set_trace_callback(trace)
        return connection


class GateProbeSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path):
        self.active = 0
        self.peak_active = 0
        self._probe_lock = threading.Lock()
        super().__init__(path)

    def note_run(self, *args, **kwargs):
        with self._probe_lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.004)
            return super().note_run(*args, **kwargs)
        finally:
            with self._probe_lock:
                self.active -= 1


class BlockingSkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path):
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def note_run(self, *args, **kwargs):
        self.started.set()
        if not self.release.wait(timeout=3.0):
            raise TimeoutError("test did not release skill finalization")
        return super().note_run(*args, **kwargs)


class SpySkills(BundledAdaptiveSkillLibrary):
    def __init__(self, path):
        self.async_note_calls = 0
        self.sync_note_calls = 0
        super().__init__(path)

    async def note_run_async(self, *args, **kwargs):
        self.async_note_calls += 1
        return await super().note_run_async(*args, **kwargs)

    def note_run(self, *args, **kwargs):
        self.sync_note_calls += 1
        return super().note_run(*args, **kwargs)


class BaseSpySkills(AdaptiveSkillLibrary):
    def __init__(self, path):
        self.sync_note_calls = 0
        super().__init__(path)

    def note_run(self, *args, **kwargs):
        self.sync_note_calls += 1
        return super().note_run(*args, **kwargs)


def test_async_note_run_preserves_one_writer_transaction(tmp_path):
    async def exercise():
        skills = TracingSkills(tmp_path / "skills.db")
        before = skills.policy(DOMAIN)
        skills.immediate_begins = 0
        await skills.note_run_async(DOMAIN, success=False, skill_used=False)
        after = skills.policy(DOMAIN)
        return skills, before, after

    skills, before, after = asyncio.run(exercise())
    assert skills.immediate_begins == 1
    assert after["updates"] == before["updates"] + 1
    assert after["exploration"] > before["exploration"]


def test_async_skill_gate_queues_before_executor(tmp_path):
    async def exercise():
        skills = GateProbeSkills(tmp_path / "gate.db")
        skills.policy(DOMAIN)
        await asyncio.gather(
            *(skills.note_run_async(DOMAIN, success=False, skill_used=False) for _ in range(12))
        )
        return skills

    skills = asyncio.run(exercise())
    assert skills.peak_active == 1
    assert skills.policy(DOMAIN)["updates"] == 12


def test_skill_cancellation_waits_for_durable_policy_update(tmp_path):
    async def exercise():
        skills = BlockingSkills(tmp_path / "cancel.db")
        before = skills.policy(DOMAIN)["updates"]
        task = asyncio.create_task(
            skills.note_run_async(DOMAIN, success=False, skill_used=False)
        )
        for _ in range(300):
            if skills.started.is_set():
                break
            await asyncio.sleep(0.001)
        assert skills.started.is_set()
        task.cancel()
        skills.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return skills, before

    skills, before = asyncio.run(exercise())
    assert skills.policy(DOMAIN)["updates"] == before + 1


def test_engine_uses_async_skill_note_only_for_sinkless_builtin(tmp_path):
    async def exercise():
        sinkless_skills = SpySkills(tmp_path / "sinkless.db")
        sinkless_engine = EcomEvoEngine(
            tmp_path / "sinkless.db",
            plugin_overrides={"memory.skills": sinkless_skills},
        )
        sinkless = await sinkless_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint=DOMAIN,
        )
        assert sinkless.event_chain_valid is True
        assert sinkless.skills_used == []
        assert sinkless_skills.async_note_calls == 1
        assert sinkless_skills.sync_note_calls == 1

        streaming_skills = SpySkills(tmp_path / "streaming.db")
        streaming_engine = EcomEvoEngine(
            tmp_path / "streaming.db",
            plugin_overrides={"memory.skills": streaming_skills},
        )

        async def sink(_event_type: str, _payload: dict):
            return None

        streaming = await streaming_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            sink=sink,
            domain_hint=DOMAIN,
        )
        assert streaming.event_chain_valid is True
        assert streaming.skills_used == []
        assert streaming_skills.async_note_calls == 0
        assert streaming_skills.sync_note_calls == 1

        base_skills = BaseSpySkills(tmp_path / "base.db")
        base_engine = EcomEvoEngine(
            tmp_path / "base.db",
            plugin_overrides={"memory.skills": base_skills},
        )
        base = await base_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint=DOMAIN,
        )
        assert base.event_chain_valid is True
        assert base.skills_used == []
        assert base_skills.sync_note_calls == 1

    asyncio.run(exercise())
