# Multi-node Certification Contract v0

## Purpose

EcomEvo currently refuses multi-node startup because the durable control plane is certified only for one application node. The migration readiness model already names six cross-node certification gates that must eventually pass after shared storage capabilities exist.

This contract makes those gates machine-readable and deliberately keeps every gate **not passed**. It is not a simulator, test runner, release toggle, or evidence-import API.

## Real-node requirement

Every certification gate requires evidence from at least two distinct application nodes executing against the future shared backend. The following are explicitly insufficient:

- two workers in one process;
- multiple processes on one application host;
- static environment/configuration declarations;
- backend capability self-attestation;
- client-supplied “pass” claims or evidence blobs;
- historical single-node CI;
- matching local fingerprints without shared-backend execution.

The contract does not discover replicas and does not run any side-effecting certification workload. Until a future trusted certification runner exists and the shared-backend prerequisites are actually implemented, every gate reports `passed=false` and `status=not_run_against_real_multi_node_backend`.

## Gates

### Cross-node durable job lease handoff

Required observations include:

- node A claims and renews one durable job;
- node B cannot claim while A’s lease is authoritatively valid;
- B can reclaim after A loses ownership;
- a stale A write is rejected by fencing; and
- provider/tool side effects are not duplicated.

### Cross-node BusinessAction CAS

Two distinct nodes must contend on the same BusinessAction transition. Exactly one authoritative terminal transition may commit, stale/duplicate transitions must be rejected, and the durable audit order must agree across nodes.

### Cross-node event reconnect

Different nodes must append/serve one conversation while event cursors remain unique and monotonic in the shared domain. An `after_id` reconnect served by another node must return the complete ordered suffix with no gaps or duplicates.

### Cross-node immutable asset integrity

An asset accepted on node A must be consumable on node B by shared immutable identity, without relying on A’s local filesystem path. Node B must verify the expected SHA-256 and fail closed on missing or mismatched bytes.

### Cross-node authority consistency

Two serving nodes must resolve one authoritative durable Policy / Runtime Skill / routing / evolution state, observe one ordered mutation history, converge without split-brain or stale-authority serving, and account separately for process-local plugin identity.

### Cross-node failure recovery

One node must be lost during or after governed side-effect dispatch. A successor must preserve ambiguous outcomes as uncertain, must not blindly replay the side effect, and must use business-state reconciliation or idempotent/fencing semantics to prevent duplicates.

## API

Admin-only, read-only endpoint:

`GET /api/runtime/readiness/multi-node/certification-contract`

The endpoint accepts no node URLs, evidence payloads, capability claims, or pass/fail input from the client.

## Authority boundary

The contract cannot execute certification tests, call tools, approve BusinessActions, change Policy/routing/Runtime Skills, change storage or topology, grant release authority, or merge/deploy code.

A future certification implementation must remain fail closed: all migration prerequisites must first be genuinely satisfied, actual replica discovery/shared-backend execution must be available, and all six real-node gates must pass before multi-node release authority can even be considered.
