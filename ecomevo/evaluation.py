from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable

from ecomevo.runtime import EcomEvoEngine


REQUIRED_CASE_FIELDS = {
    "id",
    "domain",
    "text",
    "asset_text",
    "expected_status",
    "missing_contains",
}


def packaged_gold_text() -> str:
    return files("ecomevo.evals").joinpath("gold_set.jsonl").read_text(encoding="utf-8")


def gold_text(path: str | Path | None = None) -> str:
    if path is None:
        return packaged_gold_text()
    return Path(path).read_text(encoding="utf-8")


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    text = gold_text(path)
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        row = json.loads(raw)
        missing = REQUIRED_CASE_FIELDS - set(row)
        if missing:
            label = str(path) if path is not None else "packaged:gold_set.jsonl"
            raise ValueError(f"{label}:{line_no} missing fields: {sorted(missing)}")
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
            failures.append(
                f"missing evidence does not contain {expected!r}: {summary.missing_evidence!r}"
            )
    if not summary.event_chain_valid:
        failures.append("event chain invalid")
    if not summary.stop_reason:
        failures.append("empty stop reason")
    if summary.tool_cost_used > summary.tool_cost_budget + 1e-9:
        failures.append(
            f"tool budget exceeded: {summary.tool_cost_used}>{summary.tool_cost_budget}"
        )
    for action in summary.proposed_actions:
        if action.side_effect and not action.requires_confirmation:
            failures.append(f"unconfirmed side effect proposal: {action.action_id}")
        if action.status not in {"proposed"}:
            failures.append(
                f"runtime executed or mutated action autonomously: {action.action_id}:{action.status}"
            )
    if summary.status != "completed" and summary.proposed_actions:
        failures.append("incomplete evidence produced business actions")
    return failures


def _case_result(case: dict[str, Any], summary, failures: list[str]) -> dict[str, Any]:
    return {
        "id": str(case["id"]),
        "domain": str(case["domain"]),
        "expected_status": str(case["expected_status"]),
        "status": str(summary.status),
        "verifier_score": float(summary.verifier_score),
        "stop_reason": str(summary.stop_reason or ""),
        "missing_evidence": list(summary.missing_evidence),
        "action_count": len(summary.proposed_actions),
        "event_chain_valid": bool(summary.event_chain_valid),
        "tool_cost_used": float(summary.tool_cost_used),
        "tool_cost_budget": float(summary.tool_cost_budget),
        "failures": list(failures),
    }


def _comparisons(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(phases) < 2:
        return []
    first = {row["id"]: row for row in phases[0]["cases"]}
    second = {row["id"]: row for row in phases[1]["cases"]}
    rows: list[dict[str, Any]] = []
    for case_id in sorted(set(first) & set(second)):
        a = first[case_id]
        b = second[case_id]
        status_same = a["status"] == b["status"]
        missing_same = a["missing_evidence"] == b["missing_evidence"]
        action_same = a["action_count"] == b["action_count"]
        rows.append(
            {
                "id": case_id,
                "stable": bool(status_same and missing_same and action_same),
                "fresh_status": a["status"],
                "replay_status": b["status"],
                "fresh_missing_evidence": a["missing_evidence"],
                "replay_missing_evidence": b["missing_evidence"],
                "fresh_action_count": a["action_count"],
                "replay_action_count": b["action_count"],
                "score_delta": round(float(b["verifier_score"]) - float(a["verifier_score"]), 6),
            }
        )
    return rows


def _summary(cases: list[dict[str, Any]], phases: list[dict[str, Any]], comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    failed_ids = {
        row["id"]
        for phase in phases
        for row in phase["cases"]
        if row.get("failures")
    }
    domains: dict[str, int] = {}
    for case in cases:
        domain = str(case["domain"])
        domains[domain] = domains.get(domain, 0) + 1
    return {
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(failed_ids),
        "failed_case_count": len(failed_ids),
        "drift_case_count": sum(1 for row in comparisons if not row["stable"]),
        "phase_count": len(phases),
        "domains": domains,
    }


async def evaluate_cases(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    case_list = [dict(row) for row in cases]
    if not case_list:
        raise ValueError("gold set is empty")
    with tempfile.TemporaryDirectory(prefix="ecomevo-gold-") as tmp:
        db = Path(tmp) / "runtime.db"
        phases: list[dict[str, Any]] = []
        for phase_name in ("fresh", "persisted_replay"):
            engine = EcomEvoEngine(db)
            case_results: list[dict[str, Any]] = []
            phase_failures: list[str] = []
            for case in case_list:
                assets = [asset_for(case)] if case.get("asset_text") else []
                summary = await engine.run(
                    str(case["text"]),
                    assets,
                    domain_hint=str(case["domain"]),
                )
                failures = validate(case, summary)
                case_results.append(_case_result(case, summary, failures))
                phase_failures.extend(
                    f"{case['id']}: {failure}" for failure in failures
                )
            phases.append(
                {
                    "phase": phase_name,
                    "cases": case_results,
                    "failures": phase_failures,
                }
            )
    failures = [
        f"{phase['phase']}: {failure}"
        for phase in phases
        for failure in phase["failures"]
    ]
    comparisons = _comparisons(phases)
    return {
        "ok": not failures,
        "case_count": len(case_list),
        "phase_count": len(phases),
        "phases": phases,
        "comparisons": comparisons,
        "summary": _summary(case_list, phases, comparisons),
        "failures": failures,
    }


async def evaluate(path: str | Path | None = None) -> dict[str, Any]:
    fixture_text = gold_text(path)
    cases = load_cases(path)
    result = await evaluate_cases(cases)
    result["source_hash"] = source_hash(fixture_text)
    result["fixture_snapshot"] = cases
    return result


class EvaluationRunStore:
    """Append-only SQLite snapshots for synthetic release-gate evaluations."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_runs(
                    id TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    case_count INTEGER NOT NULL,
                    failed_case_count INTEGER NOT NULL,
                    drift_case_count INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_evaluation_runs_created ON evaluation_runs(created_at DESC)"
            )

    def record(self, result: dict[str, Any]) -> dict[str, Any]:
        snapshot = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
        run_id = f"eval-{uuid.uuid4().hex[:16]}"
        created_at = time.time()
        summary = snapshot.get("summary") or {}
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO evaluation_runs(id,source_hash,ok,case_count,failed_case_count,drift_case_count,result_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    str(snapshot.get("source_hash") or ""),
                    1 if snapshot.get("ok") else 0,
                    int(snapshot.get("case_count") or 0),
                    int(summary.get("failed_case_count") or 0),
                    int(summary.get("drift_case_count") or 0),
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
        return {"id": run_id, "created_at": created_at, **snapshot}

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,source_hash,ok,case_count,failed_case_count,drift_case_count,created_at FROM evaluation_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "source_hash": str(row["source_hash"]),
                "ok": bool(row["ok"]),
                "case_count": int(row["case_count"]),
                "failed_case_count": int(row["failed_case_count"]),
                "drift_case_count": int(row["drift_case_count"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,result_json,created_at FROM evaluation_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(str(row["result_json"]))
        return {"id": str(row["id"]), "created_at": float(row["created_at"]), **payload}


class EvaluationCenter:
    """Product wrapper around the deterministic Gold Set gate.

    Evaluations always execute against a temporary runtime database. They can observe
    release behavior but cannot mutate the production runtime, routing priors, skills,
    governance state, or approval authority.
    """

    def __init__(self, db_path: str | Path):
        self.store = EvaluationRunStore(db_path)

    def catalog(self) -> dict[str, Any]:
        text = packaged_gold_text()
        cases = load_cases()
        return {
            "source_hash": source_hash(text),
            "case_count": len(cases),
            "cases": cases,
        }

    async def run(self) -> dict[str, Any]:
        result = await evaluate()
        return self.store.record(result)
