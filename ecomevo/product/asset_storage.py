from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE_RE = re.compile(r"^[0-9a-f]{24}$")
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def tenant_namespace(tenant_id: str) -> str:
    value = str(tenant_id or "").strip()
    if not value:
        raise ValueError("tenant id is required for asset object storage")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def content_address_for_digest(tenant_id: str, digest: str) -> str:
    normalized = str(digest or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("asset object digest must be a lowercase SHA-256")
    namespace = tenant_namespace(tenant_id)
    return f"tenant/{namespace}/sha256/{normalized[:2]}/{normalized}"


def digest_from_object_key(object_key: str) -> str:
    parts = str(object_key or "").split("/")
    if (
        len(parts) != 6
        or parts[0] != "tenant"
        or not _NAMESPACE_RE.fullmatch(parts[1])
        or parts[2] != "sha256"
        or len(parts[3]) != 2
        or not _SHA256_RE.fullmatch(parts[4])
        or parts[3] != parts[4][:2]
        or not _OBJECT_ID_RE.fullmatch(parts[5])
    ):
        raise ValueError("invalid asset object key")
    return parts[4]


def _namespace_from_object_key(object_key: str) -> str:
    parts = str(object_key or "").split("/")
    digest_from_object_key(object_key)
    return parts[1]


def new_object_key(tenant_id: str, digest: str) -> str:
    return f"{content_address_for_digest(tenant_id, digest)}/{uuid.uuid4().hex}"


@dataclass(frozen=True)
class AssetObjectRef:
    object_key: str
    sha256: str
    path: str


class LocalContentAddressedAssetStore:
    """Tenant-scoped local immutable objects whose identity is bound to SHA-256.

    Objects deliberately do not physically deduplicate across asset rows. The SHA-256
    content address is part of every object key, while a unique immutable object id keeps
    physical deletion race-free until a future backend provides durable shared reference
    reservations. This is a migration step toward shared object storage, not evidence that
    multiple application nodes can see the same bytes.
    """

    BACKEND = "node_local_hash_addressed_object_filesystem"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.staging_root = self.root / "staging"
        self.objects_root = self.root / "objects"
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)

    def staging_path(self, suffix: str = "") -> Path:
        safe_suffix = str(suffix or "")[:20]
        if safe_suffix and (not safe_suffix.startswith(".") or "/" in safe_suffix or "\\" in safe_suffix):
            raise ValueError("invalid staging suffix")
        return self.staging_root / f"upload-{uuid.uuid4().hex}{safe_suffix}"

    def path_for_key(self, object_key: str) -> Path:
        digest_from_object_key(object_key)
        candidate = (self.objects_root / object_key).resolve()
        root = self.objects_root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("asset object key escapes storage root")
        return candidate

    def identity_matches(
        self,
        *,
        tenant_id: str,
        object_key: str,
        sha256: str,
        path: str | Path,
    ) -> bool:
        normalized = str(sha256 or "").strip().lower()
        try:
            if digest_from_object_key(object_key) != normalized:
                return False
            if _namespace_from_object_key(object_key) != tenant_namespace(tenant_id):
                return False
            expected_path = self.path_for_key(object_key).resolve()
        except ValueError:
            return False
        return Path(path).resolve() == expected_path

    def commit(self, source: str | Path, *, tenant_id: str, sha256: str) -> AssetObjectRef:
        source_path = Path(source)
        normalized = str(sha256 or "").strip().lower()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("asset object digest must be a lowercase SHA-256")
        actual = file_sha256(source_path)
        if actual != normalized:
            raise ValueError("asset staging digest does not match expected SHA-256")

        destination: Path | None = None
        object_key = ""
        for _ in range(3):
            object_key = new_object_key(tenant_id, normalized)
            destination = self.path_for_key(object_key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Staging and object roots share the same configured asset filesystem. A hard
                # link makes the immutable object visible atomically and never overwrites an
                # existing object. Unique object ids avoid shared-reference deletion races.
                os.link(source_path, destination)
                break
            except FileExistsError:
                destination = None
                continue
        if destination is None or not destination.is_file():
            raise RuntimeError("could not allocate a unique immutable asset object")
        try:
            if file_sha256(destination) != normalized:
                destination.unlink(missing_ok=True)
                raise RuntimeError("committed asset object failed hash verification")
        finally:
            source_path.unlink(missing_ok=True)

        return AssetObjectRef(
            object_key=object_key,
            sha256=normalized,
            path=str(destination),
        )

    def verify(self, *, tenant_id: str, object_key: str, sha256: str, path: str | Path) -> bool:
        normalized = str(sha256 or "").strip().lower()
        if not self.identity_matches(
            tenant_id=tenant_id,
            object_key=object_key,
            sha256=normalized,
            path=path,
        ):
            return False
        candidate = Path(path)
        return candidate.is_file() and file_sha256(candidate) == normalized

    @staticmethod
    def capabilities() -> dict[str, object]:
        return {
            "schema_version": 1,
            "backend": LocalContentAddressedAssetStore.BACKEND,
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
