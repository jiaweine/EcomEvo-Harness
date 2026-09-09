from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssetBindingConflict(Exception):
    kind: str
    asset_id: str
    name: str = ""


def bind_assets_atomically(store: Any, asset_ids: list[str], cid: str) -> None:
    """Validate every referenced asset, then bind all unassigned rows in one transaction."""
    ids = list(dict.fromkeys(asset_ids))
    if not ids:
        return

    pending: list[str] = []
    with store._conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for asset_id in ids:
            row = connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            if not row:
                raise KeyError(asset_id)
            owner = row["conversation_id"]
            name = str(row["name"] or "")
            if owner not in (None, cid):
                raise AssetBindingConflict("foreign", asset_id, name)
            if "active" in row.keys() and not bool(row["active"]):
                raise AssetBindingConflict("inactive", asset_id, name)
            if owner is None:
                pending.append(asset_id)

        for asset_id in pending:
            cursor = connection.execute(
                "UPDATE assets SET conversation_id=? WHERE id=? AND conversation_id IS NULL",
                (cid, asset_id),
            )
            if cursor.rowcount != 1:
                raise AssetBindingConflict("changed", asset_id)
