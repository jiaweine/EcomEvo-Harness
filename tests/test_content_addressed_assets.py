from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException

from ecomevo.api.upload_security import public_asset
from ecomevo.product import ConversationStore
from ecomevo.product.asset_binding import bind_assets_atomically
from ecomevo.product.asset_storage import (
    LocalContentAddressedAssetStore,
    content_address_for_digest,
    file_sha256,
    tenant_namespace,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path / "product.db", tmp_path / "assets")


def _add_text_asset(store: ConversationStore, cid: str, data: bytes, name: str = "asset.txt"):
    source = store.asset_dir / f"source-{name}"
    source.write_bytes(data)
    return store.add_asset(
        cid,
        name=name,
        mime="text/plain",
        path=str(source),
        size=len(data),
        meta={"kind": "text", "sha256": _digest(data)},
    )


def test_content_address_is_deterministic_and_tenant_namespaced(tmp_path):
    data = b"same immutable evidence"
    digest = _digest(data)
    objects = LocalContentAddressedAssetStore(tmp_path / "assets")

    assert tenant_namespace("tenant-a") == tenant_namespace("tenant-a")
    assert tenant_namespace("tenant-a") != tenant_namespace("tenant-b")
    assert content_address_for_digest("tenant-a", digest) != content_address_for_digest("tenant-b", digest)

    a = objects.staging_path(".txt")
    b = objects.staging_path(".txt")
    a.write_bytes(data)
    b.write_bytes(data)
    ref_a = objects.commit(a, tenant_id="tenant-a", sha256=digest)
    ref_b = objects.commit(b, tenant_id="tenant-b", sha256=digest)

    assert ref_a.object_key != ref_b.object_key
    assert ref_a.path != ref_b.path
    assert ref_a.object_key.startswith(content_address_for_digest("tenant-a", digest) + "/")
    assert ref_b.object_key.startswith(content_address_for_digest("tenant-b", digest) + "/")
    assert file_sha256(ref_a.path) == file_sha256(ref_b.path) == digest
    assert objects.capabilities()["shared_across_application_nodes"] is False
    assert objects.capabilities()["cross_node_supported"] is False


def test_concurrent_same_tenant_commits_keep_independent_immutable_objects(tmp_path):
    data = b"concurrent immutable evidence bytes"
    digest = _digest(data)
    objects = LocalContentAddressedAssetStore(tmp_path / "assets")
    sources = []
    for _ in range(8):
        source = objects.staging_path(".txt")
        source.write_bytes(data)
        sources.append(source)

    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(
            pool.map(
                lambda path: objects.commit(path, tenant_id="tenant-a", sha256=digest),
                sources,
            )
        )

    content_address = content_address_for_digest("tenant-a", digest)
    assert len({ref.object_key for ref in refs}) == 8
    assert len({ref.path for ref in refs}) == 8
    assert all(ref.object_key.startswith(content_address + "/") for ref in refs)
    assert all(file_sha256(ref.path) == digest for ref in refs)
    assert all(not source.exists() for source in sources)


def test_store_promotes_same_bytes_to_independent_deletable_objects(tmp_path):
    store = _store(tmp_path)
    conv_a = store.create_conversation("a", "merchant_review")
    conv_b = store.create_conversation("b", "merchant_review")
    data = b"merchant evidence with independent object instances"

    asset_a = _add_text_asset(store, conv_a["id"], data, "a.txt")
    asset_b = _add_text_asset(store, conv_b["id"], data, "b.txt")

    assert asset_a["path"] != asset_b["path"]
    assert asset_a["meta"]["object_key"] != asset_b["meta"]["object_key"]
    assert asset_a["meta"]["content_hash_bound"] is True
    assert asset_a["meta"]["storage_backend"] == "node_local_hash_addressed_object_filesystem"
    assert Path(asset_a["path"]).is_file()
    assert Path(asset_b["path"]).is_file()
    assert "/objects/tenant/" in asset_a["path"].replace("\\", "/")

    public = public_asset(asset_a)
    assert "path" not in public
    assert "object_key" not in public["meta"]
    assert "storage_backend" not in public["meta"]
    assert "content_hash_bound" not in public["meta"]

    path_a = Path(asset_a["path"])
    path_b = Path(asset_b["path"])
    deleted_a = store.delete_asset_if_unreferenced(asset_a["id"])
    assert deleted_a
    assert not path_a.exists()
    assert path_b.is_file()
    assert store.get_asset(asset_b["id"])["path"] == asset_b["path"]

    deleted_b = store.delete_asset_if_unreferenced(asset_b["id"])
    assert deleted_b
    assert not path_b.exists()


def test_same_digest_in_different_tenants_never_shares_physical_object(tmp_path):
    store = _store(tmp_path)
    conv_a = store.create_conversation(
        "tenant-a",
        "merchant_review",
        tenant_id="tenant-a",
        created_by="admin-a",
    )
    conv_b = store.create_conversation(
        "tenant-b",
        "merchant_review",
        tenant_id="tenant-b",
        created_by="admin-b",
    )
    data = b"identical bytes across tenant boundary"

    asset_a = _add_text_asset(store, conv_a["id"], data, "tenant-a.txt")
    asset_b = _add_text_asset(store, conv_b["id"], data, "tenant-b.txt")

    assert asset_a["meta"]["sha256"] == asset_b["meta"]["sha256"]
    assert asset_a["meta"]["object_key"] != asset_b["meta"]["object_key"]
    assert Path(asset_a["path"]).resolve() != Path(asset_b["path"]).resolve()
    assert tenant_namespace("tenant-a") in asset_a["meta"]["object_key"]
    assert tenant_namespace("tenant-b") in asset_b["meta"]["object_key"]
    assert file_sha256(asset_a["path"]) == file_sha256(asset_b["path"]) == _digest(data)


def test_legacy_unassigned_asset_remains_bindable_without_frozen_tenant_namespace(tmp_path):
    store = _store(tmp_path)
    data = b"legacy pending evidence"
    source = store.asset_dir / "pending.txt"
    source.write_bytes(data)
    asset = store.add_asset(
        None,
        name="pending.txt",
        mime="text/plain",
        path=str(source),
        size=len(data),
        meta={
            "kind": "text",
            "sha256": _digest(data),
            "object_key": "caller-must-not-control-this",
            "content_hash_bound": True,
        },
    )

    assert asset["conversation_id"] is None
    assert asset["path"] == str(source)
    assert "object_key" not in asset["meta"]
    assert "content_hash_bound" not in asset["meta"]

    conv = store.create_conversation("bound later", "merchant_review")
    bind_assets_atomically(store, [asset["id"]], conv["id"])
    bound = store.get_asset(asset["id"])
    assert bound["conversation_id"] == conv["id"]
    assert bound["path"] == str(source)
    assert "object_key" not in bound["meta"]


def test_video_keyframes_receive_hash_addresses_and_physical_cleanup(tmp_path):
    store = _store(tmp_path)
    conv = store.create_conversation("video", "content_audit")
    main_bytes = b"fake-video-container-for-store-test"
    frame_bytes = [b"frame-one", b"frame-two"]
    main = store.asset_dir / "video.mp4"
    frame_dir = store.asset_dir / "video_frames"
    frame_dir.mkdir()
    main.write_bytes(main_bytes)
    frames = []
    frame_hashes = {}
    for index, data in enumerate(frame_bytes, 1):
        frame = frame_dir / f"frame_{index}.jpg"
        frame.write_bytes(data)
        frames.append(str(frame))
        frame_hashes[str(frame)] = _digest(data)

    asset = store.add_asset(
        conv["id"],
        name="video.mp4",
        mime="video/mp4",
        path=str(main),
        size=len(main_bytes),
        meta={
            "kind": "video",
            "sha256": _digest(main_bytes),
            "keyframes": frames,
            "keyframe_sha256": frame_hashes,
        },
    )

    assert not main.exists()
    assert not frame_dir.exists()
    assert asset["meta"]["object_key"]
    promoted_frames = asset["meta"]["keyframes"]
    assert len(promoted_frames) == 2
    assert all(Path(value).is_file() for value in promoted_frames)
    assert set(asset["meta"]["keyframe_object_keys"]) == set(promoted_frames)
    assert all("/objects/tenant/" in value.replace("\\", "/") for value in promoted_frames)

    assert store.delete_asset_if_unreferenced(asset["id"])
    assert not Path(asset["path"]).exists()
    assert all(not Path(value).exists() for value in promoted_frames)


def test_store_rejection_rolls_back_new_object_without_orphan(tmp_path):
    store = _store(tmp_path)
    conv = store.create_conversation("blocked", "merchant_review")
    store.list_assets(conv["id"])
    lease = store.claim_turn(conv["id"])
    assert lease

    data = b"must not become an orphan object"
    source = store.asset_dir / "blocked.txt"
    source.write_bytes(data)
    objects_root = store.asset_dir / "objects"
    before = {path for path in objects_root.rglob("*") if path.is_file()}

    with pytest.raises(HTTPException) as exc:
        store.add_asset(
            conv["id"],
            name="blocked.txt",
            mime="text/plain",
            path=str(source),
            size=len(data),
            meta={"kind": "text", "sha256": _digest(data)},
        )
    assert exc.value.status_code == 409
    after = {path for path in objects_root.rglob("*") if path.is_file()}
    assert after == before


def test_durable_snapshot_binds_server_side_object_key(tmp_path):
    store = _store(tmp_path)
    conv = store.create_conversation("snapshot", "merchant_review")
    data = b"durable snapshot evidence"
    asset = _add_text_asset(store, conv["id"], data)

    store.list_assets(conv["id"])
    lease = store.claim_turn(conv["id"])
    assert lease
    _user, _accepted, job = store.accept_message_job(
        conv["id"],
        lease_token=lease,
        content="审核资料",
        asset_ids=[asset["id"]],
        provider="demo",
        domain="merchant_review",
        history=[],
        asset_snapshot=[
            {
                "id": asset["id"],
                "sha256": asset["meta"]["sha256"],
                "name": asset["name"],
            }
        ],
    )

    snapshot = job["payload"]["assets"][0]
    assert snapshot["object_key"] == asset["meta"]["object_key"]
    assert snapshot["sha256"] == asset["meta"]["sha256"]


def test_asset_identity_tampering_fails_closed_on_read(tmp_path):
    store = _store(tmp_path)
    conv = store.create_conversation("tamper", "risk_review")
    asset = _add_text_asset(store, conv["id"], b"immutable risk evidence")

    replacement = store.asset_dir / "replacement.txt"
    replacement.write_bytes(b"different bytes")
    with store._conn() as db:
        db.execute("UPDATE assets SET path=? WHERE id=?", (str(replacement), asset["id"]))

    with pytest.raises(RuntimeError, match="hash-addressed object identity"):
        store.get_asset(asset["id"])


def test_store_capabilities_do_not_claim_dedup_or_shared_backend(tmp_path):
    store = _store(tmp_path)
    capabilities = store.asset_storage_capabilities()

    assert capabilities == {
        "schema_version": 1,
        "backend": "node_local_hash_addressed_object_filesystem",
        "identity_scheme": "tenant_namespace_plus_sha256_plus_object_id",
        "content_hash_bound": True,
        "hash_verified_on_commit": True,
        "tenant_physical_namespace": True,
        "physical_deduplication": False,
        "shared_reference_protocol": False,
        "safe_immediate_physical_delete": True,
        "shared_across_application_nodes": False,
        "cross_node_supported": False,
    }
