# Runtime Authority Snapshot v0

## Purpose

The current production runtime stores Policy, Runtime Skill, evolution, adaptive routing, and Harness state in the same node-local `runtime.db`. That is useful single-node transactional coherence, but it is not proof that two application nodes would resolve one shared authority state.

Runtime Authority Snapshot v0 adds a deterministic, read-only identity for the durable authority-bearing state that already exists in that SQLite database. It is migration evidence only; it does not enable multi-node startup.

## Snapshot surfaces

One deferred SQLite read transaction fingerprints these durable surfaces:

- `policy_versions`
- `runtime_skills`
- `evolution_policy`
- `routing_policy`
- `routing_tool_stats`
- `harness_components`

Each surface is canonicalized in a deterministic order and represented externally only by:

- row count; and
- SHA-256 digest.

The top-level snapshot SHA-256 binds the six surface digests and counts.

Raw policy rules, controls, skill guidance, routing matrices, tool statistics, or Harness content are not returned by the snapshot API.

## What is deliberately excluded

Outcome/history tables are not authority identity:

- skill outcomes
- routing outcomes
- Harness outcome/replay history
- EventStore task history

Those histories may drive future learning writes, but merely appending history must not make two otherwise identical current authority states appear different.

Process-local plugin replacement/lifecycle state is also not covered by this durable snapshot. The response says `process_plugin_lifecycle_covered=false` rather than pretending SQLite can fingerprint in-memory plugin identity.

## Fail-closed behavior

All six expected durable tables must exist in the same SQLite snapshot. If any expected table is missing, the service returns `snapshot_status=unavailable_missing_tables` and no top-level fingerprint.

The service opens SQLite with `query_only=ON` and uses a deferred read transaction. It has no write, publish, promotion, approval, tool-call, storage-migration, or topology-change path.

## Multi-node boundary

The current backend remains node-local SQLite WAL.

The snapshot explicitly reports:

- `cross_node_shared=false`
- `cross_node_supported=false`
- `multi_node_certification=false`
- `fingerprint_equality_proves_shared_transaction_domain=false`
- `self_attested_backend_capabilities_accepted=false`

Two nodes producing the same fingerprint would show that the selected durable rows currently match. It would **not** prove linearizable shared storage, atomic cross-node writes, replica freshness, or one authoritative transaction domain.

The existing `shared_runtime_authority_state` multi-node prerequisite therefore remains unsatisfied until a genuinely shared backend exists and the cross-node authority-consistency certification gate passes on real nodes.

## API

Admin-only, read-only endpoint:

`GET /api/runtime/readiness/runtime-authority`

The endpoint inherits the existing `/api/runtime` identity/RBAC boundary and does not accept tenant, backend, or capability claims from the client.
