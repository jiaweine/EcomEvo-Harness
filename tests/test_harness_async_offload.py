from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer
from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer


DOMAIN = "merchant_review"


class TracingHarness(BundledHarnessEvolutionOptimizer):
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


class GateProbeHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.active = 0
        self.peak_active = 0
        self._probe_lock = threading.Lock()
        super().__init__(path)

    def record_outcome(self, *args, **kwargs):
        with self._probe_lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.004)
            return super().record_outcome(*args, **kwargs)
        finally:
            with self._probe_lock:
                self.active -= 1


class BlockingOutcomeHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def record_outcome(self, *args, **kwargs):
        self.started.set()
        if not self.release.wait(timeout=3.0):
            raise TimeoutError("test did not release harness outcome")
        return super().record_outcome(*args, **kwargs)


class SpyHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.async_outcome_calls = 0
        self.async_state_calls = 0
        self.sync_state_calls = 0
        self.snapshot_calls = 0
        super().__init__(path)

    async def record_outcome_async(self, *args, **kwargs):
        self.async_outcome_calls += 1
        return await super().record_outcome_async(*args, **kwargs)

    async def state_summary_async(self, *args, **kwargs):
        self.async_state_calls += 1
        return await super().state_summary_async(*args, **kwargs)

    def state_summary(self, *args, **kwargs):
        self.sync_state_calls += 1
        return super().state_summary(*args, **kwargs)

    def snapshot(self, *args, **kwargs):
        self.snapshot_calls += 1
        return super().snapshot(*args, **kwargs)


def _profile(harness, session_key: str = "probe"):
    return harness.profile(DOMAIN, session_key=session_key)


def _projection(snapshot: dict) -> dict:
    components = list(snapshot.get("components") or [])
    return {
        "active": {
            row["kind"]: row["generation"]
            for row in components
            if row.get("status") == "active"
        },
        "shadow": [
            {"kind": row["kind"], "generation": row["generation"]}
            for row in components
            if row.get("status") == "shadow"
        ],
    }


def test_async_outcome_preserves_one_writer_transaction_and_compact_state(tmp_path):
    async def exercise():
        harness = TracingHarness(tmp_path / "harness.db")
        profile = _profile(harness)
        harness.immediate_begins = 0
        transitions = await harness.record_outcome_async(
            DOMAIN,
            profile["component_ids"],
            verifier_score=0.84,
            evidence_complete=True,
            session_id="s1",
        )
        state = await harness.state_summary_async(DOMAIN)
        return harness, transitions, state

    harness, transitions, state = asyncio.run(exercise())
    assert harness.immediate_begins == 1
    assert transitions == []
    assert state == _projection(harness.snapshot(DOMAIN))
    assert set(state["active"]) == set(harness.KINDS)


def test_async_harness_gate_queues_before_executor(tmp_path):
    async def exercise():
        harness = GateProbeHarness(tmp_path / "gate.db")
        profile = _profile(harness)
        await asyncio.gather(
            *(
                harness.record_outcome_async(
                    DOMAIN,
                    profile["component_ids"],
                    verifier_score=0.75,
                    evidence_complete=True,
                    session_id=f"s{index}",
                )
                for index in range(12)
            )
        )
        return harness

    harness = asyncio.run(exercise())
    assert harness.peak_active == 1


def test_harness_cancellation_waits_for_durable_outcome(tmp_path):
    async def exercise():
        harness = BlockingOutcomeHarness(tmp_path / "cancel.db")
        profile = _profile(harness)
        before = {
            row["component_id"]: row["uses"]
            for row in harness.snapshot(DOMAIN)["components"]
            if row["component_id"] in set(profile["component_ids"])
        }
        task = asyncio.create_task(
            harness.record_outcome_async(
                DOMAIN,
                profile["component_ids"],
                verifier_score=0.8,
                evidence_complete=True,
                session_id="cancelled",
            )
        )
        for _ in range(300):
            if harness.started.is_set():
                break
            await asyncio.sleep(0.001)
        assert harness.started.is_set()
        task.cancel()
        harness.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return harness, profile, before

    harness, profile, before = asyncio.run(exercise())
    after = {
        row["component_id"]: row["uses"]
        for row in harness.snapshot(DOMAIN)["components"]
        if row["component_id"] in set(profile["component_ids"])
    }
    assert after == {component_id: uses + 1 for component_id, uses in before.items()}


def test_engine_uses_harness_async_paths_only_for_sinkless_builtin(tmp_path):
    async def exercise():
        sinkless_harness = SpyHarness(tmp_path / "sinkless.db")
        sinkless_engine = EcomEvoEngine(
            tmp_path / "sinkless.db",
            plugin_overrides={"evolver.harness": sinkless_harness},
        )
        sinkless = await sinkless_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint=DOMAIN,
        )
        assert sinkless.event_chain_valid is True
        assert sinkless_harness.async_outcome_calls == 1
        assert sinkless_harness.async_state_calls == 1
        assert sinkless_harness.snapshot_calls == 0

        streaming_harness = SpyHarness(tmp_path / "streaming.db")
        streaming_engine = EcomEvoEngine(
            tmp_path / "streaming.db",
            plugin_overrides={"evolver.harness": streaming_harness},
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
        assert streaming_harness.async_outcome_calls == 0
        assert streaming_harness.async_state_calls == 0
        assert streaming_harness.sync_state_calls == 1
        assert streaming_harness.snapshot_calls == 0

        base_harness = HarnessEvolutionOptimizer(tmp_path / "base.db")
        base_engine = EcomEvoEngine(
            tmp_path / "base.db",
            plugin_overrides={"evolver.harness": base_harness},
        )
        base = await base_engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint=DOMAIN,
        )
        assert base.event_chain_valid is True
        assert "harness_evolution" in base.belief.facts

    asyncio.run(exercise())
