from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .asset_storage import LocalContentAddressedAssetStore, digest_from_object_key
from .guarded_store import ConversationStore as GuardedConversationStore
from .store import ConversationStore as BaseConversationStore
from .tenant_store import TenantConversationStore


_ORIGINAL_BASE_ADD_ASSET = BaseConversationStore.add_asset
_ORIGINAL_GUARDED_ADD_ASSET = GuardedConversationStore.add_asset
_ORIGINAL_BASE_GET_ASSET = BaseConversationStore.get_asset
_ORIGINAL_TENANT_GET_ASSET = TenantConversationStore.get_asset
_ORIGINAL_ACCEPT_MESSAGE_JOB = GuardedConversationStore.accept_message_job
_INTERNAL_OBJECT_META = {
    "object_key",
    "storage_backend",
    "content_hash_bound",
    "keyframe_object_keys",
}


def _conversation_tenant(self, cid: str | None) -> str:
    if not cid:
        return "local"
    with self._conn() as db:
        columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(conversations)").fetchall()}
        if "tenant_id" in columns:
            row = db.execute("SELECT tenant_id FROM conversations WHERE id=?", (cid,)).fetchone()
            if not row:
                raise KeyError(cid)
            return str(row["tenant_id"] or "local")
        row = db.execute("SELECT 1 FROM conversations WHERE id=?", (cid,)).fetchone()
        if not row:
            raise KeyError(cid)
    return "local"


def _cleanup_empty_source_parent(root: Path, source: Path) -> None:
    try:
        root = root.resolve()
        parent = source.resolve().parent
        if parent == root or not parent.is_relative_to(root):
            return
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return


def _prune_empty_parents(root: Path, path: Path) -> None:
    try:
        root = root.resolve()
        parent = path.resolve().parent
        while parent != root and parent.is_relative_to(root):
            if not parent.is_dir() or any(parent.iterdir()):
                break
            next_parent = parent.parent
            parent.rmdir()
            parent = next_parent
    except OSError:
        return


def _referenced_file_paths(self) -> set[Path]:
    referenced: set[Path] = set()
    with self._conn() as db:
        rows = db.execute("SELECT path,meta FROM assets").fetchall()
    for row in rows:
        raw_path = str(row["path"] or "")
        if raw_path:
            try:
                referenced.add(Path(raw_path).resolve())
            except OSError:
                pass
        try:
            metadata = json.loads(row["meta"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        for value in metadata.get("keyframes") or []:
            try:
                referenced.add(Path(str(value)).resolve())
            except OSError:
                continue
    return referenced


def _discard_unreferenced_new_objects(self, paths: list[Path]) -> None:
    """Rollback promotion only for unique object paths still absent from asset references."""
    if not paths:
        return
    root = Path(self.asset_dir).resolve()
    objects_root = (root / "objects").resolve()
    try:
        referenced = _referenced_file_paths(self)
    except Exception:
        # Cleanup must never mask the original store/database failure. Unique objects may
        # remain orphaned for later maintenance, but referenced bytes are never guessed away.
        return
    for path in paths:
        try:
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(objects_root)
                or resolved in referenced
                or not resolved.is_file()
            ):
                continue
            resolved.unlink(missing_ok=True)
            _prune_empty_parents(root, resolved)
        except OSError:
            continue


def _promote_asset_files(
    self,
    cid: str | None,
    *,
    path: str,
    meta: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], list[Path]]:
    metadata = dict(meta or {})
    source = Path(str(path or ""))

    # Legacy/unassigned assets may later be atomically bound to a tenant conversation.
    # Do not freeze a tenant namespace before that binding exists, and never accept an
    # externally supplied internal object identity on this compatibility path.
    if not cid:
        for key in _INTERNAL_OBJECT_META:
            metadata.pop(key, None)
        return str(path), metadata, []

    digest = str(metadata.get("sha256") or "").strip().lower()
    if len(digest) != 64 or not source.is_file():
        return str(path), metadata, []

    tenant_id = _conversation_tenant(self, cid)
    objects = LocalContentAddressedAssetStore(self.asset_dir)
    existing_object_key = str(metadata.get("object_key") or "")
    if existing_object_key:
        if not objects.identity_matches(
            tenant_id=tenant_id,
            object_key=existing_object_key,
            sha256=digest,
            path=source,
        ):
            raise RuntimeError("existing asset path does not match its hash-addressed object identity")
        if not objects.verify(
            tenant_id=tenant_id,
            object_key=existing_object_key,
            sha256=digest,
            path=source,
        ):
            raise RuntimeError("existing hash-addressed asset object failed verification")
        return str(source), metadata, []

    created_paths: list[Path] = []
    try:
        ref = objects.commit(source, tenant_id=tenant_id, sha256=digest)
        created_paths.append(Path(ref.path))
        metadata.update(
            {
                "object_key": ref.object_key,
                "storage_backend": objects.BACKEND,
                "content_hash_bound": True,
            }
        )

        frames = [str(value) for value in (metadata.get("keyframes") or [])]
        frame_hashes = dict(metadata.get("keyframe_sha256") or {})
        if frames:
            promoted_frames: list[str] = []
            promoted_hashes: dict[str, str] = {}
            frame_object_keys: dict[str, str] = {}
            for value in frames:
                frame_source = Path(value)
                frame_digest = str(frame_hashes.get(value) or "").strip().lower()
                if len(frame_digest) == 64 and frame_source.is_file():
                    frame_ref = objects.commit(
                        frame_source,
                        tenant_id=tenant_id,
                        sha256=frame_digest,
                    )
                    created_paths.append(Path(frame_ref.path))
                    promoted_frames.append(frame_ref.path)
                    promoted_hashes[frame_ref.path] = frame_digest
                    frame_object_keys[frame_ref.path] = frame_ref.object_key
                    _cleanup_empty_source_parent(Path(self.asset_dir), frame_source)
                else:
                    promoted_frames.append(value)
                    if frame_digest:
                        promoted_hashes[value] = frame_digest
            metadata["keyframes"] = promoted_frames
            metadata["keyframe_sha256"] = promoted_hashes
            if frame_object_keys:
                metadata["keyframe_object_keys"] = frame_object_keys

        return ref.path, metadata, created_paths
    except Exception:
        _discard_unreferenced_new_objects(self, created_paths)
        raise


def _validate_asset_object_identity(self, asset: dict[str, Any]) -> dict[str, Any]:
    metadata = asset.get("meta") or {}
    object_key = str(metadata.get("object_key") or "")
    if not object_key:
        return asset
    digest = str(metadata.get("sha256") or "").strip().lower()
    try:
        key_digest = digest_from_object_key(object_key)
    except ValueError as exc:
        raise RuntimeError("asset object key is invalid") from exc
    if key_digest != digest:
        raise RuntimeError("asset object key does not match recorded SHA-256")
    tenant_id = _conversation_tenant(self, asset.get("conversation_id"))
    objects = LocalContentAddressedAssetStore(self.asset_dir)
    if not objects.identity_matches(
        tenant_id=tenant_id,
        object_key=object_key,
        sha256=digest,
        path=str(asset.get("path") or ""),
    ):
        raise RuntimeError("asset path does not match its hash-addressed object identity")
    return asset


def _base_add_asset(self, cid, *, name, mime, path, size, meta):
    object_path, object_meta, created_paths = _promote_asset_files(
        self,
        cid,
        path=path,
        meta=meta,
    )
    try:
        return _ORIGINAL_BASE_ADD_ASSET(
            self,
            cid,
            name=name,
            mime=mime,
            path=object_path,
            size=size,
            meta=object_meta,
        )
    except Exception:
        _discard_unreferenced_new_objects(self, created_paths)
        raise


def _guarded_add_asset(self, cid, *, name, mime, path, size, meta):
    object_path, object_meta, created_paths = _promote_asset_files(
        self,
        cid,
        path=path,
        meta=meta,
    )
    try:
        return _ORIGINAL_GUARDED_ADD_ASSET(
            self,
            cid,
            name=name,
            mime=mime,
            path=object_path,
            size=size,
            meta=object_meta,
        )
    except Exception:
        _discard_unreferenced_new_objects(self, created_paths)
        raise


def _base_get_asset(self, aid):
    return _validate_asset_object_identity(self, _ORIGINAL_BASE_GET_ASSET(self, aid))


def _tenant_get_asset(self, aid):
    return _validate_asset_object_identity(self, _ORIGINAL_TENANT_GET_ASSET(self, aid))


def _accept_message_job(
    self,
    cid: str,
    *,
    lease_token: str,
    content: str,
    asset_ids: list[str],
    provider: str,
    domain: str,
    history: list[dict[str, Any]],
    asset_snapshot: list[dict[str, Any]],
):
    bound_snapshot: list[dict[str, Any]] = []
    for snapshot in asset_snapshot:
        current = self.get_asset(str(snapshot.get("id") or ""))
        if current.get("conversation_id") != cid:
            raise HTTPException(409, "任务资料归属已发生变化，请重新发送")
        current_meta = current.get("meta") or {}
        expected_sha = str(snapshot.get("sha256") or "")
        current_sha = str(current_meta.get("sha256") or "")
        if expected_sha and current_sha and expected_sha != current_sha:
            raise HTTPException(409, "任务资料内容指纹已发生变化，请重新发送")
        row = dict(snapshot)
        object_key = str(current_meta.get("object_key") or "")
        if object_key:
            row["object_key"] = object_key
        bound_snapshot.append(row)

    return _ORIGINAL_ACCEPT_MESSAGE_JOB(
        self,
        cid,
        lease_token=lease_token,
        content=content,
        asset_ids=asset_ids,
        provider=provider,
        domain=domain,
        history=history,
        asset_snapshot=bound_snapshot,
    )


def _safe_cleanup_asset_files(self, asset: dict[str, Any]) -> None:
    root = Path(self.asset_dir).resolve()
    referenced = _referenced_file_paths(self)
    candidates: set[Path] = set()
    raw_path = str(asset.get("path") or "")
    if raw_path:
        candidates.add(Path(raw_path))
    metadata = asset.get("meta") or {}
    for value in metadata.get("keyframes") or []:
        candidates.add(Path(str(value)))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if (
                not resolved.is_relative_to(root)
                or resolved in referenced
                or not resolved.is_file()
            ):
                continue
            resolved.unlink(missing_ok=True)
            _prune_empty_parents(root, resolved)
        except OSError:
            continue


def _asset_storage_capabilities(self) -> dict[str, object]:
    return LocalContentAddressedAssetStore.capabilities()


# Base-store tests and the guarded production store both receive hash-bound object promotion.
# The tenant subclass has its own get_asset implementation, so identity validation is patched
# there as well. Final composed Product stores inherit these class methods dynamically.
BaseConversationStore.add_asset = _base_add_asset
GuardedConversationStore.add_asset = _guarded_add_asset
BaseConversationStore.get_asset = _base_get_asset
TenantConversationStore.get_asset = _tenant_get_asset
GuardedConversationStore.accept_message_job = _accept_message_job
BaseConversationStore._safe_cleanup_asset_files = _safe_cleanup_asset_files
BaseConversationStore.asset_storage_capabilities = _asset_storage_capabilities
