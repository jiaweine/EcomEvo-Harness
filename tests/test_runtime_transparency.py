import pytest

from ecomevo.runtime import EcomEvoEngine


@pytest.mark.asyncio
async def test_runtime_summary_exposes_verifiable_stop_and_budget_state(tmp_path):
    engine = EcomEvoEngine(tmp_path / 'runtime.db')
    summary = await engine.run('这个商家能过吗', [], domain_hint='merchant_review')

    assert summary.status == 'needs_evidence'
    assert summary.evidence_complete is False
    assert summary.missing_evidence == summary.belief.missing_evidence
    assert summary.stop_reason in {'evidence_incomplete', 'stagnated', 'budget_exhausted', 'step_limit'}
    assert summary.stop_detail
    assert summary.tool_cost_budget >= summary.tool_cost_used >= 0
    assert summary.tool_cost_remaining == pytest.approx(
        max(0.0, summary.tool_cost_budget - summary.tool_cost_used), abs=0.001
    )
    assert summary.autonomy_mode == 'deterministic_fallback'
    assert summary.belief.facts['stop_reason'] == summary.stop_reason
    assert summary.belief.facts['tool_cost_used'] == summary.tool_cost_used

    completed = [event for event in engine.events.list_events(summary.session_id) if event.event_type == 'run.completed']
    assert len(completed) == 1
    payload = completed[0].payload
    assert payload['stop_reason'] == summary.stop_reason
    assert payload['missing_evidence'] == summary.missing_evidence
    assert payload['tool_cost_budget'] == summary.tool_cost_budget


@pytest.mark.asyncio
async def test_verified_runtime_uses_verified_stop_reason(tmp_path):
    engine = EcomEvoEngine(tmp_path / 'runtime.db')
    asset = {
        'id': 'asset-merchant',
        'name': 'merchant.txt',
        'mime': 'text/plain',
        'path': '',
        'size': 64,
        'meta': {
            'kind': 'text',
            'text': '营业执照 91310000123456789A 品牌授权书齐全 无历史处罚',
            'search_text': '营业执照 91310000123456789A 品牌授权书齐全 无历史处罚',
        },
    }
    summary = await engine.run('审核这个商家的主体、授权和历史风险', [asset], domain_hint='merchant_review')

    if summary.status == 'completed':
        assert summary.stop_reason == 'verified'
        assert summary.evidence_complete is True
        assert summary.missing_evidence == []
