from fastapi import HTTPException
import pytest

from ecomevo.product import ConversationStore


def test_running_turn_rejects_new_asset_until_lease_is_released(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    conversation = store.create_conversation()
    cid = conversation["id"]
    source = tmp_path / "asset.txt"
    source.write_text("evidence", encoding="utf-8")

    lease = store.claim_turn(cid)
    assert lease
    with pytest.raises(HTTPException) as exc:
        store.add_asset(
            cid,
            name="asset.txt",
            mime="text/plain",
            path=str(source),
            size=source.stat().st_size,
            meta={},
        )
    assert exc.value.status_code == 409
    assert store.list_assets(cid) == []

    assert store.release_turn(cid, lease)
    row = store.add_asset(
        cid,
        name="asset.txt",
        mime="text/plain",
        path=str(source),
        size=source.stat().st_size,
        meta={},
    )
    assert row["conversation_id"] == cid
    assert len(store.list_assets(cid)) == 1


def test_turn_claim_rejects_asset_snapshot_changed_by_another_store(tmp_path):
    db = tmp_path / "product.db"
    asset_dir = tmp_path / "assets"
    reader = ConversationStore(db, asset_dir)
    writer = ConversationStore(db, asset_dir)
    cid = reader.create_conversation()["id"]

    first = tmp_path / "first.txt"
    first.write_text("first", encoding="utf-8")
    writer.add_asset(cid, name="first.txt", mime="text/plain", path=str(first), size=5, meta={})

    snapshot = reader.list_assets(cid)
    assert [row["name"] for row in snapshot] == ["first.txt"]

    second = tmp_path / "second.txt"
    second.write_text("second", encoding="utf-8")
    writer.add_asset(cid, name="second.txt", mime="text/plain", path=str(second), size=6, meta={})

    with pytest.raises(HTTPException) as exc:
        reader.claim_turn(cid)
    assert exc.value.status_code == 409
    assert "资料刚刚发生变化" in exc.value.detail
    assert reader.has_active_turn(cid) is False

    refreshed = reader.list_assets(cid)
    assert {row["name"] for row in refreshed} == {"first.txt", "second.txt"}
    lease = reader.claim_turn(cid)
    assert lease
    assert reader.release_turn(cid, lease)
