from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from ecomevo.runtime import EcomEvoEngine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "evals" / "gold_set.jsonl"


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        row = json.loads(raw)
        required = {"id", "domain", "text", "asset_text", "expected_status", "missing_contains"}
        missing = required - set(row)
        if missing:
            raise ValueError(f"{path}:{line_no} missing fields: {sorted(missing)}")
        rows.append(row)
    if not rows:
        raise ValueError("gold set is empty")
    return rows


def asset_for(case: dict[str, Any]) -> dict[str, Any]:
    text = str(case.get("asset_text") or "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "id": f"gold-{case['id']}",
        "name": f"{case['id']}.txt",
        "mime": "text/plain",
        "path": "",
        "size": len(text.encode("utf-8")),
        "meta": {
            "kind": "text",
            "text": text,
            "search_text": text,
            "sha256": digest,
        },
    }


def validate(case: dict[str, Any], summary) -> list[str]:
    failures: list[str] = []
    if summary.domain.value != case["domain"]:
        failures.append(f"domain={summary.domain.value!r}, expected={case['domain']!r}")
    if summary.status != case["expected_status"]:
        failures.append(f"status={summary.status!r}, expected={case['expected_status']!r}")
    for expected in case.get("missing_contains") or []:
        if expected not in summary.missing_evidence:
            failures.append(f"missing evidence does not contain {expected!r}: {summary.missing_evidence!r}")
    if not summary.event_chain_valid:
        failures.append("event chain invalid")
    if not summary.stop_reason:
        failures.append("empty stop reason")
    if summary.tool_cost_used > summary.tool_cost_budget + 1e-9:
        failures.append(f"tool budget exceeded: {summary.tool_cost_used}>{summary.tool_cost_budget}")
    for action in summary.proposed_actions:
        if action.side_effect and not action.requires_confirmation:
            failures.append(f"unconfirmed side effect proposal: {action.action_id}")
        if action.status not in {"proposed"}:
            failures.append(f"runtime executed or mutated action autonomously: {action.action_id}:{action.status}")
    if summary.status != "completed" and summary.proposed_actions:
        failures.append("incomplete evidence produced business actions")
    return failures


async def evaluate(path: Path) -> dict[str, Any]:
    cases = load_cases(path)
    with tempfile.TemporaryDirectory(prefix="ecomevo-gold-") as tmp:
        db = Path(tmp) / "runtime.db"
        phases: list[dict[str, Any]] = []
        # Run once from fresh priors, then reconstruct the Engine from the same DB and
        # replay the exact Gold Set. The second pass is the promotion gate: persisted
        # routing/skill/evolution state must not weaken evidence or authority invariants.
        for phase in ("fresh", "persisted_replay"):
            engine = EcomEvoEngine(db)
            case_results = []
            phase_failures = []
            for case in cases:
                assets = [asset_for(case)] if case.get("asset_text") else []
                summary = await engine.run(
                    str(case["text"]),
                    assets,
                    domain_hint=str(case["domain"]),
                )
                failures = validate(case, summary)
                case_results.append({
                    "id": case["id"],
                    "status": summary.status,
                    "score": summary.verifier_score,
                    "stop_reason": summary.stop_reason,
                    "missing_evidence": summary.missing_evidence,
                    "failures": failures,
                })
                phase_failures.extend(f"{case['id']}: {failure}" for failure in failures)
            phases.append({"phase": phase, "cases": case_results, "failures": phase_failures})
        failures = [f"{phase['phase']}: {failure}" for phase in phases for failure in phase["failures"]]
        return {
            "ok": not failures,
            "case_count": len(cases),
            "phase_count": len(phases),
            "phases": phases,
            "failures": failures,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the EcomEvo business Gold Set promotion gate")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.gold))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
