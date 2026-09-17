from __future__ import annotations

from typing import Any


def _precedence(row: dict[str, Any]) -> tuple[int, int, int]:
    value = row.get("precedence") or {}
    return (
        int(value.get("authority", row.get("authority", 0)) or 0),
        int(value.get("specificity", row.get("specificity", 0)) or 0),
        int(value.get("priority", row.get("priority", 0)) or 0),
    )


def runtime_policy_view(resolution: dict[str, Any]) -> dict[str, Any]:
    """Return the policy payload that runtime reasoning is allowed to consume.

    ``PolicyStore.resolve`` intentionally retains every applicable document for audit.
    Natural-language rules are less machine-composable than structured controls, so the
    runtime view only exposes rule prose from the globally highest-precedence document
    tier. Lower-precedence documents stay in ``policies`` for provenance, while their
    non-conflicting structured controls may still contribute through per-control
    precedence. A conflicted resolution exposes no executable natural-language rules.
    """

    result = dict(resolution or {})
    policies = [dict(row) for row in (result.get("policies") or []) if isinstance(row, dict)]
    result["policies"] = policies
    result["policy_documents"] = [str(row.get("version_id") or "") for row in policies if row.get("version_id")]
    result["controls_authority"] = "structured_controls"

    status = str(result.get("status") or "missing")
    if status != "resolved" or not policies:
        result["rules"] = []
        result["rule_policy_versions"] = []
        return result

    top = max(_precedence(row) for row in policies)
    sources = [row for row in policies if _precedence(row) == top]
    rules: list[str] = []
    seen: set[str] = set()
    for row in sources:
        for rule in row.get("rules") or []:
            text = str(rule).strip()
            if text and text not in seen:
                seen.add(text)
                rules.append(text)

    result["rules"] = rules
    result["rule_policy_versions"] = [str(row.get("version_id")) for row in sources if row.get("version_id")]
    return result
