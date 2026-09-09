from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

from ecomevo.api import application
from ecomevo.product.asset_binding import bind_assets_atomically
from ecomevo.product.guarded_store import ConversationStore


class TracedConversationStore(ConversationStore):
    def __init__(self, *args, **kwargs):
        self.statements: list[str] = []
        super().__init__(*args, **kwargs)

    def _conn(self):
        connection = super()._conn()
        connection.set_trace_callback(self.statements.append)
        return connection


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


def test_atomic_bind_uses_one_writer_transaction_for_thirty_assets(tmp_path):
    store = TracedConversationStore(tmp_path / "product.db", tmp_path / "assets")
    target = store.create_conversation("target", "merchant_review")
    asset_ids: list[str] = []
    for index in range(30):
        path = tmp_path / f"asset-{index}.txt"
        path.write_text(str(index), encoding="utf-8")
        asset = store.add_asset(
            None,
            name=path.name,
            mime="text/plain",
            path=str(path),
            size=1,
            meta={},
        )
        asset_ids.append(asset["id"])

    store.statements.clear()
    bind_assets_atomically(store, asset_ids, target["id"])

    begins = [statement for statement in store.statements if statement.strip().upper() == "BEGIN IMMEDIATE"]
    assert len(begins) == 1
    assert all(store.get_asset(asset_id)["conversation_id"] == target["id"] for asset_id in asset_ids)
