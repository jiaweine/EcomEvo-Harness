# Admin Control State Manifest v0

## Purpose

The current control plane persists release and governance state in multiple node-local SQLite databases. That is compatible with the certified single-node deployment, but it is not a shared admin transaction domain and cannot support a multi-node certification claim.

Admin Control State Manifest v0 adds a deterministic, read-only fingerprint over the durable admin databases that already exist. It is migration evidence only. It does not migrate storage, enable multi-node startup, or grant release authority.

## Covered databases

The manifest requires all six expected databases and validates a known schema marker in each one:

- `evaluation.db` — `evaluation_runs`
- `knowledge.db` — `knowledge_sources`
- `decision_exports.db` — `decision_exports`
- `release_readiness.db` — `release_readiness_snapshots`
- `skill_studio.db` — `studio_skill_versions`
- `connection_governance.db` — `connection_probe_history`

`connection_governance.db` is included because durable MCP connection probe evidence participates in release review.

## Fingerprint semantics

Each database is opened independently with SQLite read-only URI mode plus `PRAGMA query_only=ON`.

For each database, one deferred read transaction hashes the logical SQLite dump with SHA-256. The API exposes only:

- database id and fixed filename;
- availability status;
- SHA-256 digest;
- user-table count; and
- dump-statement count.

Raw evaluation results, knowledge content, decision-export payloads, release snapshots, Skill Studio content, connection probe errors, database paths, and SQLite error messages are not returned.

When all six database snapshots are available, the top-level SHA-256 binds the ordered six database summaries.

## Fail-closed behavior

A top-level manifest hash is produced only when every expected database:

1. exists;
2. can be opened read-only;
3. passes SQLite `quick_check`;
4. contains its expected schema marker; and
5. can be fully traversed for the logical dump.

Missing files, missing schema markers, corruption, or read errors make the top-level manifest unavailable. The API does not convert partial evidence into a positive readiness result.

## Cross-database boundary

The six SQLite files do not share one transaction boundary. Each digest is internally consistent for its own read transaction, but the top-level manifest is a sequential observation across databases.

The response therefore explicitly reports:

- `cross_database_atomic_snapshot=false`
- `shared_across_application_nodes=false`
- `cross_node_supported=false`
- `fingerprint_equality_proves_shared_admin_transaction_domain=false`
- `self_attested_backend_capabilities_accepted=false`
- `multi_node_requirement_id=shared_admin_control_state`
- `requirement_current_satisfied=false`
- `multi_node_ready=false`

Matching manifests can show that the selected local durable state matched at observation time. They cannot prove linearizable shared storage, one atomic admin transaction domain, replica freshness, or safe cross-node mutation semantics.

The existing `shared_admin_control_state` multi-node prerequisite therefore remains unsatisfied until the admin/release-evidence stores use genuinely shared durable semantics and the relevant real multi-node certification gates pass.

## Authority boundary

The manifest is observational only. It cannot:

- mutate admin or release evidence state;
- publish knowledge;
- change Skill Studio candidates;
- create decision exports;
- change connection governance;
- change the storage backend or runtime topology;
- grant release or BusinessAction authority;
- execute tools; or
- merge/deploy code.

## API

Admin-only, read-only endpoint:

`GET /api/runtime/readiness/admin-control-state`

The endpoint inherits the existing `/api/runtime` identity/RBAC boundary and accepts no database path, backend capability, or readiness claim from the client.
