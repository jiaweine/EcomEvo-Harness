from __future__ import annotations

import asyncio

from ecomevo.runtime import EcomEvoEngine


def test_task_target_terms_are_exact_and_copy_safe(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    policy = engine.autonomy.policy

    assert policy._task_target_terms.get() is None
    uncached = policy._terms("target-alpha")
    assert policy._task_target_terms.get() is None

    token = policy.bind_task_target_terms()
    try:
        first = policy._terms("target-alpha")
        assert first == uncached
        first.add("__caller_mutation__")
        second = policy._terms("target-alpha")
        assert second == uncached
        assert "__caller_mutation__" not in second

        cache = policy._task_target_terms.get()
        assert cache is not None
        assert set(cache) == {"target-alpha"}

        policy._terms("target-beta")
        assert set(cache) == {"target-alpha", "target-beta"}
    finally:
        policy.reset_task_target_terms(token)

    assert policy._task_target_terms.get() is None


def test_task_target_terms_are_context_isolated(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    policy = engine.autonomy.policy

    async def worker(value: str):
        token = policy.bind_task_target_terms()
        try:
            policy._terms(value)
            await asyncio.sleep(0)
            cache = policy._task_target_terms.get()
            assert cache is not None
            return set(cache)
        finally:
            policy.reset_task_target_terms(token)

    async def run_workers():
        return await asyncio.gather(worker("task-alpha"), worker("task-beta"))

    first, second = asyncio.run(run_workers())
    assert first == {"task-alpha"}
    assert second == {"task-beta"}
    assert policy._task_target_terms.get() is None


def test_controller_binds_and_resets_target_terms_for_real_run(tmp_path):
    engine = EcomEvoEngine(tmp_path / "runtime.db")
    controller = engine.autonomy
    policy = controller.policy
    calls = {"bind": 0, "reset": 0}

    original_bind = policy.bind_task_target_terms
    original_reset = policy.reset_task_target_terms

    def bind():
        calls["bind"] += 1
        return original_bind()

    def reset(token):
        calls["reset"] += 1
        return original_reset(token)

    policy.bind_task_target_terms = bind
    policy.reset_task_target_terms = reset
    try:
        summary = asyncio.run(
            engine.run(
                "审核商家并核对主体、授权和历史风险。task-local term lifecycle test。",
                [],
                domain_hint="merchant_review",
            )
        )
    finally:
        policy.bind_task_target_terms = original_bind
        policy.reset_task_target_terms = original_reset

    assert summary.event_chain_valid
    assert calls == {"bind": 1, "reset": 1}
    assert policy._task_target_terms.get() is None
