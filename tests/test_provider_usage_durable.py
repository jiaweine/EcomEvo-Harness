from __future__ import annotations

import pytest

from ecomevo.api.durable_jobs import DurableConversationWorker
from ecomevo.product.guarded_store import ConversationStore
from ecomevo.providers.telemetry import record_provider_usage


class UsageAnalyzer:
    async def run(self, **_kwargs):
        record_provider_usage(
            provider="openai",
            model="gpt-test",
            source="durable-test",
            usage={
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            },
        )
        return {
            "actions": [],
            "session_id": "usage-session",
            "domain": "merchant_review",
            "runtime": {"status": "completed"},
            "answer": "done",
        }


class NoopMCP:
    def action_binding(self, *_args, **_kwargs):
        return None


async def _emit(store, cid, event_type, payload, job_id=None, worker_id=None):
    if job_id and worker_id:
        return store.add_job_event(job_id, worker_id, event_type, payload)
    return store.add_event(cid, event_type, payload)


@pytest.mark.asyncio
async def test_durable_worker_persists_request_local_provider_usage(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    conv = store.create_conversation("usage", "merchant_review")
    store.list_assets(conv["id"])
    turn = store.claim_turn(conv["id"])
    assert turn
    _, _, job = store.accept_message_job(
        conv["id"],
        lease_token=turn,
        content="审核商家",
        asset_ids=[],
        provider="openai",
        domain="merchant_review",
        history=[],
        asset_snapshot=[],
    )

    worker = DurableConversationWorker(
        store,
        UsageAnalyzer(),
        NoopMCP(),
        emit=lambda cid, event_type, payload, job_id=None, worker_id=None: _emit(
            store, cid, event_type, payload, job_id, worker_id
        ),
        wake=lambda _cid: None,
    )
    claimed = store.claim_job(worker.worker_id, job_id=job["id"])
    assert claimed
    await worker._execute(claimed)

    assistant = [
        row
        for row in store.list_messages(conv["id"])
        if row["role"] == "assistant"
    ][-1]
    usage = assistant["payload"]["provider_usage"]
    assert usage["schema_version"] == 1
    assert usage["external_calls"] == 1
    assert usage["usage_reported_calls"] == 1
    assert usage["usage_coverage_rate"] == 1.0
    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == 30
    assert usage["total_tokens"] == 150
    assert usage["events"][0]["provider"] == "openai"
    assert usage["events"][0]["model"] == "gpt-test"
    assert usage["cost"]["available"] is False
    assert "price book" in usage["cost"]["reason"]
