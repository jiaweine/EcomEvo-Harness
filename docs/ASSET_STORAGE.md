# Asset Storage Contract

## Current backend

EcomEvo stores newly uploaded **conversation-bound** evidence as tenant-scoped, hash-addressed immutable local objects.

Each internal object identity binds:

- a one-way tenant namespace hash;
- the exact file SHA-256; and
- a unique immutable object instance id.

The internal object key has the form:

`tenant/<tenant-namespace>/sha256/<prefix>/<sha256>/<object-id>`

The SHA-256 prefix is deterministic for the content and tenant. The final object id is intentionally unique per asset/keyframe object. The object key and local filesystem path are server-internal metadata and are not added to the public asset metadata returned to the browser.

## Why v0 does not physically deduplicate

Physical deduplication creates a reference-lifetime problem: an object may be created before a new asset row is committed, while another request concurrently deletes its last currently visible reference. Without a durable shared reservation/reference protocol, immediate physical deletion could race with that in-flight reference.

This v0 therefore does **not** share one physical object across asset rows. Identical bytes still carry the same tenant-scoped SHA-256 content-address prefix, but each asset/keyframe receives its own immutable object instance. This preserves the existing API contract that an unreferenced asset can be physically deleted immediately without risking another asset's bytes.

A future deduplicating backend must first introduce durable object reference/reservation semantics; deduplication is not enabled merely to save disk space.

## Commit and rollback semantics

Uploads are validated and hashed before the asset row is persisted. When a valid SHA-256 is present, the store promotes the staging file to a unique hash-bound object path using non-overwriting hard-link visibility and verifies the committed bytes against the recorded SHA-256.

There is no copy/replace fallback that may overwrite an existing immutable object or expose partially written object bytes. If the subsequent store transaction rejects the asset, objects created by that attempt are removed only when no persisted asset or keyframe references them. Cleanup failure never masks the original store/database error.

Identical bytes from different tenants always use different physical namespaces. Identical bytes inside the same tenant also use distinct physical object instances until a durable shared-reference protocol exists.

Video keyframes use the same hash-bound object promotion, verification, rollback, and deletion path.

## Legacy unassigned assets

The lower-level store still supports legacy assets with `conversation_id = NULL` that may later be atomically bound to a conversation. Their tenant is not known at creation time, so v0 deliberately leaves those assets on the legacy path and strips any caller-supplied internal object identity. Binding later remains compatible and does not trigger a hidden filesystem migration during a read.

The production upload endpoint already requires a conversation id, so normal new uploads use tenant-scoped hash-addressed objects immediately.

## Durable jobs

When a durable conversation job is accepted, the server re-reads each asset and binds both its SHA-256 and, when available, its internal object key into the immutable job snapshot. The client cannot supply an authoritative object key.

Workers still verify the asset bytes against the snapshot SHA-256. Hash-addressed asset rows additionally fail closed when the recorded database path no longer matches the tenant-scoped object identity.

## Deletion

An unreferenced asset row may be deleted under the existing audit rules. Because v0 objects are physically independent, its main object and derived keyframe objects can be removed immediately without deleting another asset's bytes. Reference-aware cleanup remains in place as a defensive guard for legacy/shared paths.

## Multi-node boundary

This is a storage-identity and single-node correctness improvement, **not shared object storage**.

The backend remains on the application node's local asset filesystem. Another application node is not guaranteed to resolve the same object key to the same bytes. Therefore:

- `physical_deduplication = false`;
- `shared_reference_protocol = false`;
- `shared_across_application_nodes = false`;
- `cross_node_supported = false`;
- the `shared_immutable_asset_storage` migration requirement remains unsatisfied; and
- local hash addressing is not accepted as multi-node certification evidence.

A future multi-node backend must preserve immutable identity and hash-verification semantics while providing genuinely shared durable object visibility, then pass the real cross-node asset snapshot integrity gate.
