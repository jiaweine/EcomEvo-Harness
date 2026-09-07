from __future__ import annotations

import asyncio
import threading

from ecomevo.runtime.bundled_harness_optimizer import BundledHarnessEvolutionOptimizer
from ecomevo.runtime.engine import EcomEvoEngine
from ecomevo.runtime.harness_optimizer import HarnessEvolutionOptimizer


DOMAIN = "merchant_review"


class ThreadProbeHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.profile_threads: list[int] = []
        super().__init__(path)

    def profile(self, *args, **kwargs):
        self.profile_threads.append(threading.get_ident())
        return super().profile(*args, **kwargs)


class SpyBundledHarness(BundledHarnessEvolutionOptimizer):
    def __init__(self, path):
        self.async_profile_calls = 0
        self.sync_profile_calls = 0
        super().__init__(path)

    async def profile_async(self, *args, **kwargs):
        self.async_profile_calls += 1
        return await super().profile_async(*args, **kwargs)

    def profile(self, *args, **kwargs):
        self.sync_profile_calls += 1
        return super().profile(*args, **kwargs)


class SpyBaseHarness(HarnessEvolutionOptimizer):
    def __init__(self, path):
        self.profile_calls = 0
        super().__init__(path)

    def profile(self, *args, **kwargs):
        self.profile_calls += 1
        return super().profile(*args, **kwargs)


def test_profile_async_matches_sync_and_runs_off_loop(tmp_path):
    async def exercise():
        harness = ThreadProbeHarness(tmp_path / "profile.db")
        harness.profile(DOMAIN, session_key="warm")
        harness.profile_threads.clear()
        main_thread = threading.get_ident()
        async_result = await harness.profile_async(DOMAIN, session_key="same-session")
        async_threads = list(harness.profile_threads)
        harness.profile_threads.clear()
        sync_result = harness.profile(DOMAIN, session_key="same-session")
        return main_thread, async_threads, async_result, sync_result

    main_thread, async_threads, async_result, sync_result = asyncio.run(exercise())
    assert async_result == sync_result
    assert async_threads
    assert main_thread not in async_threads


def test_engine_uses_profile_async_only_for_sinkless_builtin(tmp_path):
    async def exercise():
        sinkless_harness = SpyBundledHarness(tmp_path / "sinkless.db")
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
        assert sinkless_harness.async_profile_calls == 1
        # profile_async delegates to the same synchronous implementation in a worker.
        assert sinkless_harness.sync_profile_calls == 1

        streaming_harness = SpyBundledHarness(tmp_path / "streaming.db")
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
        assert streaming_harness.async_profile_calls == 0
        assert streaming_harness.sync_profile_calls == 1

    asyncio.run(exercise())


def test_base_harness_plugin_keeps_sync_profile_fallback(tmp_path):
    async def exercise():
        harness = SpyBaseHarness(tmp_path / "base.db")
        engine = EcomEvoEngine(
            tmp_path / "base.db",
            plugin_overrides={"evolver.harness": harness},
        )
        summary = await engine.run(
            "审核商家并核对主体和授权资料",
            [],
            domain_hint=DOMAIN,
        )
        return harness, summary

    harness, summary = asyncio.run(exercise())
    assert summary.event_chain_valid is True
    assert harness.profile_calls == 1
    assert not hasattr(harness, "profile_async")
