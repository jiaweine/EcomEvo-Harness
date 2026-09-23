from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_internal_object_identity_is_not_added_to_public_asset_metadata():
    upload_security = (ROOT / "ecomevo" / "api" / "upload_security.py").read_text(encoding="utf-8")
    public_meta = upload_security.split("PUBLIC_ASSET_META =", 1)[1].split("}\n", 1)[0]

    assert "object_key" not in public_meta
    assert "storage_backend" not in public_meta
    assert "content_hash_bound" not in public_meta
    assert "keyframe_object_keys" not in public_meta


def test_local_hash_addressed_store_uses_atomic_visibility_without_overwrite_fallback():
    service = (ROOT / "ecomevo" / "product" / "asset_storage.py").read_text(encoding="utf-8")

    assert "os.link(source_path, destination)" in service
    assert "FileExistsError" in service
    assert "shutil.copy" not in service
    assert "shutil.copy2" not in service
    assert "os.replace" not in service
    assert '"physical_deduplication": False' in service
    assert '"shared_reference_protocol": False' in service
    assert '"safe_immediate_physical_delete": True' in service
    assert '"shared_across_application_nodes": False' in service
    assert '"cross_node_supported": False' in service


def test_durable_snapshot_binds_object_key_server_side_and_unassigned_assets_do_not_freeze_tenant():
    service = (ROOT / "ecomevo" / "product" / "content_addressed_assets.py").read_text(encoding="utf-8")

    assert 'current = self.get_asset(str(snapshot.get("id") or ""))' in service
    assert 'row["object_key"] = object_key' in service
    assert "asset_snapshot=bound_snapshot" in service
    assert "任务资料内容指纹已发生变化" in service
    assert "if not cid:" in service
    assert "metadata.pop(key, None)" in service
    assert "# nosec" not in service


def test_local_hash_addressing_cannot_unlock_multi_node_readiness():
    readiness = (ROOT / "ecomevo" / "product" / "multi_node_readiness.py").read_text(encoding="utf-8")

    assert '"id": "shared_immutable_asset_storage"' in readiness
    assert '"current_backend": "tenant_scoped_hash_addressed_node_local_filesystem"' in readiness
    assert '"current_satisfied": False' in readiness
    assert '"cross_node_asset_visibility": False' in readiness
    assert '"local_hash_addressing_is_cross_node_certification": False' in readiness
    assert '"physical_deduplication_without_reference_protocol_allowed": False' in readiness
