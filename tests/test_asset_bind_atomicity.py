from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

from ecomevo.api import application
from ecomevo.product.guarded_store import ConversationStore


def test_rejected_multi_asset_message_does_not_partially_bind_assets(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    target = store.create_conversation("target", "merchant_review")
    foreign = store.create_conversation("foreign", "merchant_review")

    unassigned_path = tmp_path / "unassigned.txt"
    unassigned_path.write_text("unassigned", encoding="utf-8")
    unassigned = store.add_asset(
        None,
        name="unassigned.txt",
        mime="text/plain",
        path=str(unassigned_path),
        size=10,
        meta={},
    )
    foreign_path = tmp_path / "foreign.txt"
    foreign_path.write_text("foreign", encoding="utf-8")
    foreign_asset = store.add_asset(
        foreign["id"],
        name="foreign.txt",
        mime="text/plain",
        path=str(foreign_path),
        size=7,
        meta={},
    )
    monkeypatch.setattr(application, "store", store)

    async def exercise():
        with pytest.raises(HTTPException) as exc:
            await application.conversation_message(
                target["id"],
                application.ChatRequest(
                    content="审核商家",
                    provider="auto",
                    asset_ids=[unassigned["id"], foreign_asset["id"]],
                ),
                BackgroundTasks(),
            )
        assert exc.value.status_code == 409

    asyncio.run(exercise())

    assert store.get_asset(unassigned["id"])["conversation_id"] is None
    assert store.list_messages(target["id"]) == []
