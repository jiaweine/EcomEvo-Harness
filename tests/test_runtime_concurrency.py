import asyncio

import pytest

from ecomevo.runtime import EcomEvoEngine


@pytest.mark.asyncio
async def test_concurrent_adaptive_runs_keep_event_chains_and_authority_boundary(tmp_path):
    engine = EcomEvoEngine(tmp_path / 'runtime.db')

    async def one(index):
        return await engine.run(
            f'审核商家并核对主体、授权和历史风险，任务 {index}',
            [],
            domain_hint='merchant_review',
        )

    summaries = await asyncio.gather(*(one(index) for index in range(24)))

    assert len({summary.session_id for summary in summaries}) == 24
    assert all(summary.event_chain_valid for summary in summaries)
    assert all(summary.status == 'needs_evidence' for summary in summaries)
    assert all(summary.evidence_complete is False for summary in summaries)
    assert all(summary.missing_evidence for summary in summaries)
    assert all(summary.stop_reason for summary in summaries)

    for summary in summaries:
        assert not any(action.status in {'approved', 'executed'} for action in summary.proposed_actions)
        events = engine.events.list_events(summary.session_id)
        assert sum(event.event_type == 'autonomy.stopped' for event in events) == 1
        assert sum(event.event_type == 'run.completed' for event in events) == 1
        assert events[-1].event_type == 'run.completed'
