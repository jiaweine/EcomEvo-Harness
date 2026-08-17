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
