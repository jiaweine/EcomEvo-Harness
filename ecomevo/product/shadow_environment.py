from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


SURFACE_MUTATIONS: dict[str, dict[str, str]] = {
    "mcp": {
        "timeout_after_dispatch": "ambiguous_side_effect",
        "connection_reset_after_dispatch": "ambiguous_side_effect",
        "http_5xx_after_dispatch": "ambiguous_side_effect",
        "malformed_success_response": "ambiguous_side_effect",
        "permission_denied": "explicit_rejection",
        "schema_remove_field": "schema_mutation",
        "schema_add_required_field": "schema_mutation",
    },
    "browser": {
        "navigation_timeout": "transport_failure",
        "element_missing": "explicit_failure",
        "stale_dom": "interface_mutation",
        "confirmation_lost_after_submit": "ambiguous_side_effect",
    },
    "terminal": {
        "timeout_after_dispatch": "ambiguous_side_effect",
        "nonzero_exit": "explicit_failure",
        "truncated_output": "ambiguous_output",
        "command_shape_changed": "interface_mutation",
    },
    "structured_data": {
        "missing_required_field": "schema_mutation",
        "type_changed": "schema_mutation",
        "stale_snapshot": "data_freshness",
        "duplicate_record": "data_quality",
    },
}

OPERATIONS = {"read", "governed_action"}
OBSERVATION_PHASES = {
    "pre_dispatch",
    "dispatch",
    "post_dispatch",
    "response",
    "parse",
    "validation",
    "unknown",
}
CORPUS_REDACTION_PROFILE = "ecomevo-shadow-v1"

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_SAFE_FAILURE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def authority_contract() -> dict[str, bool]:
    return {
        "read_only_runtime": True,
        "executes_real_tools": False,
        "launches_real_browser": False,
        "launches_real_terminal": False,
        "changes_routing": False,
        "changes_policy": False,
        "changes_runtime_skills": False,
        "approves_business_actions": False,
        "changes_business_action_state": False,
        "changes_production_authority": False,
    }


class ShadowEnterpriseSimulator:
    """Deterministic, non-executing enterprise failure/schema replay candidate builder.

    The simulator models safety expectations only. It never invokes a real MCP tool,
    browser, terminal, provider, or business system and it never writes runtime state.
    Its output is an offline replay/training candidate, not evidence that the simulated
    production integration actually behaves this way.
    """

    SCHEMA_VERSION = 1
    FIXTURE_SCHEMA_VERSION = 1

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _fingerprint(cls, value: dict[str, Any]) -> str:
        if not value:
            return ""
        return hashlib.sha256(cls._canonical(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_labels(labels: list[str] | None) -> list[str]:
        clean: list[str] = []
        seen: set[str] = set()
        for raw in labels or []:
            value = str(raw or "").strip()
            if not _LABEL_RE.fullmatch(value) or value in seen:
                continue
            seen.add(value)
            clean.append(value)
            if len(clean) >= 24:
                break
        return clean

    @staticmethod
    def _clean_target(target: str) -> str:
        value = str(target or "").strip()
        if not value or len(value) > 160 or any(ord(ch) < 32 for ch in value):
            raise ValueError("shadow target must be a printable identifier up to 160 characters")
        return value

    @staticmethod
    def _clean_opaque_id(value: str, *, field: str, pattern: re.Pattern[str]) -> str:
        cleaned = str(value or "").strip()
        if not pattern.fullmatch(cleaned):
            raise ValueError(f"{field} must be a bounded opaque identifier")
        return cleaned

    @staticmethod
    def _normalize_observed_at(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("observed_at is required")
        parse_value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(parse_value)
        except ValueError as exc:
            raise ValueError("observed_at must be ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("observed_at must include an explicit timezone")
        normalized = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        return normalized.replace("+00:00", "Z")

    @classmethod
    def catalog(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "fixture_schema_version": cls.FIXTURE_SCHEMA_VERSION,
            "surfaces": {
                surface: [
                    {"mutation": mutation, "class": mutation_class}
                    for mutation, mutation_class in sorted(mutations.items())
                ]
                for surface, mutations in sorted(SURFACE_MUTATIONS.items())
            },
            "operations": sorted(OPERATIONS),
            "corpus_import": {
                "supported": True,
                "endpoint": "/api/runtime/shadow/import-fixture",
                "redaction_profile": CORPUS_REDACTION_PROFILE,
                "accepts_raw_payload": False,
                "accepts_headers": False,
                "accepts_body": False,
                "requires_upstream_sha256": True,
                "upstream_digest_verified_by_shadow": False,
                "observation_phases": sorted(OBSERVATION_PHASES),
            },
            "authority": authority_contract(),
            "methodology": {
                "execution": "no real system is invoked; outputs are offline replay/training candidates only",
                "side_effect_ambiguity": "governed actions with ambiguous post-dispatch failure must remain uncertain and must not be auto-retried",
                "schema_mutation": "schema-mutation candidates require distinct before/after schema fingerprints",
                "corpus_import": "imports only bounded pre-redacted failure metadata; raw payloads, headers, bodies, credentials, and free-form incident text are not accepted",
                "provenance_binding": "fixture hashes bind tenant, scenario semantics, sanitized observation metadata, and a caller-attested upstream SHA-256; Shadow does not independently verify the upstream digest",
                "production_truth": "simulation output is not production evidence and cannot replace Verifier, Governance, Approval, or real integration tests",
            },
        }

    @staticmethod
    def _expected_control(operation: str, mutation_class: str) -> dict[str, Any]:
        governed = operation == "governed_action"
        ambiguous = mutation_class in {"ambiguous_side_effect", "ambiguous_output"}
        schema_like = mutation_class in {"schema_mutation", "interface_mutation"}

        if governed and ambiguous:
            runtime_outcome = "uncertain"
        elif mutation_class == "explicit_rejection":
            runtime_outcome = "failed"
        elif mutation_class == "explicit_failure":
            runtime_outcome = "failed"
        elif schema_like:
            runtime_outcome = "blocked_for_revalidation"
        elif mutation_class in {"data_freshness", "data_quality"}:
            runtime_outcome = "needs_evidence"
        elif mutation_class == "transport_failure":
            runtime_outcome = "read_failed" if not governed else "uncertain"
        else:
            runtime_outcome = "needs_review"

        return {
            "runtime_outcome": runtime_outcome,
            "automatic_retry_allowed": False,
            "requires_business_state_check": bool(governed and runtime_outcome == "uncertain"),
            "requires_schema_revalidation": bool(schema_like),
            "requires_fresh_evidence": mutation_class in {"data_freshness", "data_quality"},
            "verifier_still_authoritative": True,
            "governance_still_authoritative": True,
            "approval_still_required_for_side_effect": governed,
            "business_action_state_mutation": False,
        }

    def simulate(
        self,
        *,
        tenant_id: str,
        surface: str,
        operation: str,
        mutation: str,
        target: str,
        context_labels: list[str] | None = None,
        baseline_schema: dict[str, Any] | None = None,
        mutated_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        surface_key = str(surface or "").strip().lower()
        operation_key = str(operation or "").strip().lower()
        mutation_key = str(mutation or "").strip().lower()
        if surface_key not in SURFACE_MUTATIONS:
            raise ValueError("unsupported shadow surface")
        if operation_key not in OPERATIONS:
            raise ValueError("unsupported shadow operation")
        mutation_class = SURFACE_MUTATIONS[surface_key].get(mutation_key)
        if mutation_class is None:
            raise ValueError("unsupported mutation for shadow surface")

        target_value = self._clean_target(target)
        labels = self._clean_labels(context_labels)
        before = baseline_schema if isinstance(baseline_schema, dict) else {}
        after = mutated_schema if isinstance(mutated_schema, dict) else {}
        before_fp = self._fingerprint(before)
        after_fp = self._fingerprint(after)

        if mutation_class == "schema_mutation":
            if not before_fp or not after_fp:
                raise ValueError("schema mutation requires baseline_schema and mutated_schema")
            if before_fp == after_fp:
                raise ValueError("schema mutation requires a changed schema fingerprint")

        expected = self._expected_control(operation_key, mutation_class)
        scenario = {
            "surface": surface_key,
            "operation": operation_key,
            "mutation": mutation_key,
            "mutation_class": mutation_class,
            "target": target_value,
            "context_labels": labels,
            "baseline_schema_fingerprint": before_fp or None,
            "mutated_schema_fingerprint": after_fp or None,
        }
        semantic = {
            "schema_version": self.SCHEMA_VERSION,
            "tenant_scope": str(tenant_id),
            "scenario": scenario,
            "expected_control": expected,
        }
        digest = hashlib.sha256(self._canonical(semantic).encode("utf-8")).hexdigest()
        candidate_id = f"shadow-{digest[:24]}"

        return {
            **semantic,
            "candidate_id": candidate_id,
            "content_hash": digest,
            "replay_candidate": {
                "kind": "shadow_enterprise_failure_replay",
                "purpose": "training_or_offline_evaluation_only",
                "deterministic": True,
                "executable": False,
                "persisted_by_simulator": False,
                "invokes_real_system": False,
                "production_evidence": False,
            },
            "authority": authority_contract(),
            "limitations": {
                "does_not_measure_real_provider_behavior": True,
                "does_not_validate_real_credentials_or_permissions": True,
                "does_not_validate_real_browser_or_terminal_side_effects": True,
                "cannot_replace_verifier": True,
                "cannot_replace_business_approval": True,
            },
        }

    def import_fixture(
        self,
        *,
        tenant_id: str,
        surface: str,
        operation: str,
        mutation: str,
        target: str,
        context_labels: list[str] | None = None,
        baseline_schema: dict[str, Any] | None = None,
        mutated_schema: dict[str, Any] | None = None,
        source_system: str,
        source_event_id: str,
        observed_at: str,
        source_record_sha256: str,
        redaction_profile: str,
        redaction_attested: bool,
        phase: str,
        status_code: int | None = None,
        error_code: str | None = None,
        error_class: str | None = None,
        latency_ms: int | None = None,
    ) -> dict[str, Any]:
        """Create a deterministic, provenance-bound fixture from pre-redacted metadata.

        Raw incident payloads are intentionally not accepted by this API. The upstream
        digest is bound into the fixture, but Shadow cannot independently prove that the
        caller-supplied digest matches any external record.
        """
        if redaction_profile != CORPUS_REDACTION_PROFILE:
            raise ValueError("unsupported shadow corpus redaction profile")
        if redaction_attested is not True:
            raise ValueError("shadow corpus import requires redaction_attested=true")

        source_system_value = self._clean_opaque_id(
            source_system,
            field="source_system",
            pattern=_LABEL_RE,
        )
        source_event_value = self._clean_opaque_id(
            source_event_id,
            field="source_event_id",
            pattern=_OPAQUE_ID_RE,
        )
        digest_value = str(source_record_sha256 or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest_value):
            raise ValueError("source_record_sha256 must be a 64-character SHA-256 hex digest")

        phase_value = str(phase or "").strip().lower()
        if phase_value not in OBSERVATION_PHASES:
            raise ValueError("unsupported failure observation phase")

        normalized_status: int | None = None
        if status_code is not None:
            normalized_status = int(status_code)
            if normalized_status < 100 or normalized_status > 599:
                raise ValueError("status_code must be between 100 and 599")

        normalized_latency: int | None = None
        if latency_ms is not None:
            normalized_latency = int(latency_ms)
            if normalized_latency < 0 or normalized_latency > 3_600_000:
                raise ValueError("latency_ms must be between 0 and 3600000")

        def optional_failure_token(value: str | None, *, field: str) -> str | None:
            if value is None:
                return None
            cleaned = str(value).strip()
            if not _SAFE_FAILURE_TOKEN_RE.fullmatch(cleaned):
                raise ValueError(f"{field} must be a bounded identifier, not free-form text")
            return cleaned

        observation = {
            "phase": phase_value,
            "status_code": normalized_status,
            "error_code": optional_failure_token(error_code, field="error_code"),
            "error_class": optional_failure_token(error_class, field="error_class"),
            "latency_ms": normalized_latency,
        }

        candidate = self.simulate(
            tenant_id=tenant_id,
            surface=surface,
            operation=operation,
            mutation=mutation,
            target=target,
            context_labels=context_labels,
            baseline_schema=baseline_schema,
            mutated_schema=mutated_schema,
        )
        provenance = {
            "source_system": source_system_value,
            "source_event_id": source_event_value,
            "observed_at": self._normalize_observed_at(observed_at),
            "source_record_sha256": digest_value,
            "redaction_profile": CORPUS_REDACTION_PROFILE,
            "redaction_attested": True,
            "source_digest_verified_by_shadow": False,
        }
        fixture_semantic = {
            "fixture_schema_version": self.FIXTURE_SCHEMA_VERSION,
            "tenant_scope": str(tenant_id),
            "shadow_candidate_hash": candidate["content_hash"],
            "scenario": candidate["scenario"],
            "expected_control": candidate["expected_control"],
            "provenance": provenance,
            "sanitized_observation": observation,
        }
        fixture_hash = hashlib.sha256(
            self._canonical(fixture_semantic).encode("utf-8")
        ).hexdigest()

        return {
            **fixture_semantic,
            "fixture_id": f"shadow-fixture-{fixture_hash[:24]}",
            "fixture_hash": fixture_hash,
            "shadow_candidate_id": candidate["candidate_id"],
            "replay_fixture": {
                "kind": "shadow_enterprise_failure_fixture",
                "purpose": "training_or_offline_evaluation_only",
                "deterministic": True,
                "executable": False,
                "persisted_by_importer": False,
                "invokes_real_system": False,
                "production_evidence": False,
                "provenance_bound": True,
                "pre_redacted_metadata_only": True,
                "raw_payload_accepted": False,
            },
            "provenance_binding": {
                "hash_algorithm": "sha256",
                "binds_tenant": True,
                "binds_shadow_candidate_hash": True,
                "binds_sanitized_observation": True,
                "binds_source_record_sha256": True,
                "upstream_digest_verified_by_shadow": False,
            },
            "authority": authority_contract(),
            "limitations": {
                "upstream_digest_is_caller_attested": True,
                "raw_source_record_not_available_to_shadow": True,
                "does_not_measure_real_provider_behavior": True,
                "does_not_validate_real_credentials_or_permissions": True,
                "cannot_replace_verifier": True,
                "cannot_replace_business_approval": True,
            },
        }
