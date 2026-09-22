from __future__ import annotations

import hashlib
import json
import re
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
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


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

    @classmethod
    def catalog(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "surfaces": {
                surface: [
                    {"mutation": mutation, "class": mutation_class}
                    for mutation, mutation_class in sorted(mutations.items())
                ]
                for surface, mutations in sorted(SURFACE_MUTATIONS.items())
            },
            "operations": sorted(OPERATIONS),
            "authority": authority_contract(),
            "methodology": {
                "execution": "no real system is invoked; outputs are offline replay/training candidates only",
                "side_effect_ambiguity": "governed actions with ambiguous post-dispatch failure must remain uncertain and must not be auto-retried",
                "schema_mutation": "schema-mutation candidates require distinct before/after schema fingerprints",
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
