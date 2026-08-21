from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable

from .replay_gate import HarnessReplayGate
from .tools import _query_terms


@dataclass
class HarnessComponent:
    component_id: str
    domain: str
    kind: str
    status: str
    parent_id: str | None
    content: dict[str, Any]
    hypothesis: str
    alpha: float
    beta: float
    uses: int
    generation: int
    created_at: float
    updated_at: float

    @property
    def mean(self) -> float:
        return self.alpha / max(1e-12, self.alpha + self.beta)

    @property
    def variance(self) -> float:
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / max(1e-12, total * total * (total + 1.0))

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["posterior_mean"] = round(self.mean, 4)
        row["posterior_std"] = round(math.sqrt(max(0.0, self.variance)), 4)
        return row


@dataclass(frozen=True)
class _CohortPosterior:
    alpha: float = 1.0
    beta: float = 1.0
    exposures: int = 0

    @property
    def mean(self) -> float:
        return self.alpha / max(1e-12, self.alpha + self.beta)

    @property
    def variance(self) -> float:
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / max(1e-12, total * total * (total + 1.0))


class HarnessEvolutionOptimizer:
    """Verifier-grounded block-coordinate optimizer for EcomEvo's cognitive harness.

    This is the production harness-evolution loop. It intentionally keeps business
    authority outside the learnable state while making the cognitive substrate editable.

    Design lineage adapted for EcomEvo:
    - AHE (2026): durable component / experience / decision observability;
    - SkillOpt (2026): bounded text-space add/delete/replace edits and rejected-edit memory;
    - HarnessCompass (2026): task-agnostic constrained, component-wise evolution;
    - SBCO (2026): verifier-grounded approximate block-coordinate optimization.

    The optimizer can evolve prompt strategy, read-only tool strategy, memory retrieval and
    cognitive delegation. Registry, Sandbox, Verifier, RBAC, approval and business action
    authority are deliberately not representable in this type system.
    """

    KINDS = ("prompt", "tool", "memory", "delegation")
    FIELDS = {
        "prompt": {"guidance"},
        "tool": {"preferred_tools", "avoid_tools"},
        "memory": {"retrieval_terms", "guidance"},
        "delegation": {"roles", "guidance"},
    }

    def __init__(self, db_path: str | Path, *, sandbox=None):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            risk = float(os.environ.get("ECOMEVO_HARNESS_ACCEPT_RISK", "0.05"))
        except (TypeError, ValueError):
            risk = 0.05
        self.accept_risk = max(0.001, min(0.25, risk))
        try:
            budget = int(os.environ.get("ECOMEVO_HARNESS_EDIT_BUDGET", "3"))
        except (TypeError, ValueError):
            budget = 3
        self.edit_budget = max(1, min(8, budget))
        self.replay_gate = HarnessReplayGate(sandbox=sandbox)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _init(self) -> None:
        with self._conn() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS harness_components(
                    component_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_id TEXT,
                    content_json TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    alpha REAL NOT NULL,
                    beta REAL NOT NULL,
                    uses INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_harness_components_domain_kind_status
                    ON harness_components(domain,kind,status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS harness_component_outcomes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component_id TEXT NOT NULL,
                    session_id TEXT,
                    verifier_score REAL NOT NULL,
                    evidence_complete INTEGER NOT NULL,
                    meta_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_harness_component_outcomes_component
                    ON harness_component_outcomes(component_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS harness_rejected_edits(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS harness_replay_cases(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    trajectory_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_harness_replay_cases_domain
                    ON harness_replay_cases(domain,created_at DESC);
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any]) -> HarnessComponent:
        data = dict(row)
        return HarnessComponent(
            component_id=str(data["component_id"]),
            domain=str(data["domain"]),
            kind=str(data["kind"]),
            status=str(data["status"]),
            parent_id=data.get("parent_id"),
            content=dict(json.loads(data.get("content_json") or "{}")),
            hypothesis=str(data.get("hypothesis") or ""),
            alpha=float(data["alpha"]),
            beta=float(data["beta"]),
            uses=int(data["uses"]),
            generation=int(data["generation"]),
            created_at=float(data["created_at"]),
            updated_at=float(data["updated_at"]),
        )

    def _ensure_domain(self, connection: sqlite3.Connection, domain: str) -> None:
        now = time.time()
        for kind in self.KINDS:
            existing = connection.execute(
                "SELECT 1 FROM harness_components WHERE domain=? AND kind=? AND status='active' LIMIT 1",
                (domain, kind),
            ).fetchone()
            if existing:
                continue
            connection.execute(
                "INSERT INTO harness_components VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"hc-{uuid.uuid4().hex[:12]}",
                    domain,
                    kind,
                    "active",
                    None,
                    "{}",
                    "bootstrap empty cognitive component",
                    1.0,
                    1.0,
                    0,
                    0,
                    now,
                    now,
                ),
            )

    def _rows(self, connection: sqlite3.Connection, domain: str) -> list[HarnessComponent]:
        rows = connection.execute(
            "SELECT * FROM harness_components WHERE domain=? AND status IN ('active','shadow') ORDER BY kind,status,updated_at DESC",
            (domain,),
        ).fetchall()
        return [self._decode(row) for row in rows]

    def _active(self, connection: sqlite3.Connection, domain: str, kind: str) -> HarnessComponent:
        self._ensure_domain(connection, domain)
        row = connection.execute(
            "SELECT * FROM harness_components WHERE domain=? AND kind=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
            (domain, kind),
        ).fetchone()
        assert row is not None
        return self._decode(row)

    @staticmethod
    def _stable_unit(value: str) -> float:
        raw = hashlib.sha256(value.encode("utf-8")).digest()[:8]
        return int.from_bytes(raw, "big") / float((1 << 64) - 1)

    @staticmethod
    def verifier_potential(score: float, completeness: float) -> float:
        """Harmonic verifier potential used as the cross-task cognitive reward.

        A high model/verifier quality score cannot compensate for low evidence completeness.
        Safety and authority are not reward terms: unsafe actions never enter the search space.
        """
        q = max(0.0, min(1.0, float(score)))
        c = max(0.0, min(1.0, float(completeness)))
        denominator = q + c
        return 0.0 if denominator <= 1e-12 else (2.0 * q * c) / denominator

    @staticmethod
    def _superiority_from_stats(candidate: _CohortPosterior, incumbent: _CohortPosterior) -> float:
        variance = max(1e-12, candidate.variance + incumbent.variance)
        z = (candidate.mean - incumbent.mean) / math.sqrt(variance)
        return max(0.0, min(1.0, NormalDist().cdf(z)))

    def _cohort_posterior(
        self,
        connection: sqlite3.Connection,
        component_id: str,
        *,
        since: float,
    ) -> _CohortPosterior:
        rows = connection.execute(
            "SELECT verifier_score FROM harness_component_outcomes WHERE component_id=? AND created_at>=? ORDER BY id",
            (component_id, float(since)),
        ).fetchall()
        alpha = 1.0
        beta = 1.0
        for row in rows:
            reward = max(0.0, min(1.0, float(row["verifier_score"])))
            alpha += reward
            beta += 1.0 - reward
        return _CohortPosterior(alpha=alpha, beta=beta, exposures=len(rows))

    def _shadow_probability(
        self,
        connection: sqlite3.Connection,
        shadow: HarnessComponent,
        active: HarnessComponent,
    ) -> tuple[float, int, int]:
        candidate = self._cohort_posterior(connection, shadow.component_id, since=shadow.created_at)
        incumbent = self._cohort_posterior(connection, active.component_id, since=shadow.created_at)
        # Before both arms have evidence, use an unbiased 0.5 allocation. There is no
        # hand-written rollout percentage and no promotion/rejection can occur yet.
        if candidate.exposures == 0 or incumbent.exposures == 0:
            return 0.5, candidate.exposures, incumbent.exposures
        return self._superiority_from_stats(candidate, incumbent), candidate.exposures, incumbent.exposures

    def profile(self, domain: str, *, session_key: str) -> dict[str, Any]:
        """Bind one durable profile and allocate shadow traffic from posterior superiority."""
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_domain(connection, domain)
            rows = self._rows(connection, domain)
            by_kind: dict[str, list[HarnessComponent]] = {kind: [] for kind in self.KINDS}
            for row in rows:
                by_kind[row.kind].append(row)

            profile: dict[str, Any] = {"domain": domain, "component_ids": [], "components": {}}
            for kind in self.KINDS:
                active = next((row for row in by_kind[kind] if row.status == "active"), None)
                if active is None:
                    continue
                shadow = next((row for row in by_kind[kind] if row.status == "shadow"), None)
                chosen = active
                probability = 0.0
                candidate_exposures = incumbent_exposures = 0
                if shadow and shadow.parent_id == active.component_id:
                    probability, candidate_exposures, incumbent_exposures = self._shadow_probability(
                        connection, shadow, active
                    )
                    if self._stable_unit(f"{session_key}|{shadow.component_id}") < probability:
                        chosen = shadow
                profile["component_ids"].append(chosen.component_id)
                profile["components"][kind] = {
                    **chosen.content,
                    "component_id": chosen.component_id,
                    "status": chosen.status,
                    "generation": chosen.generation,
                    "posterior_mean": round(chosen.mean, 4),
                    "shadow_probability": round(probability, 4),
                    "shadow_candidate_exposures": candidate_exposures,
                    "shadow_incumbent_exposures": incumbent_exposures,
                }
        return profile

    def _transition_shadows(
        self,
        connection: sqlite3.Connection,
        domain: str,
        now: float,
    ) -> list[dict[str, Any]]:
        transitions: list[dict[str, Any]] = []
        shadows = connection.execute(
            "SELECT * FROM harness_components WHERE domain=? AND status='shadow' ORDER BY updated_at",
            (domain,),
        ).fetchall()
        for raw in shadows:
            candidate = self._decode(raw)
            if not candidate.parent_id:
                continue
            parent_raw = connection.execute(
                "SELECT * FROM harness_components WHERE component_id=? AND status='active'",
                (candidate.parent_id,),
            ).fetchone()
            if not parent_raw:
                continue
            parent = self._decode(parent_raw)
            probability, candidate_exposures, incumbent_exposures = self._shadow_probability(
                connection, candidate, parent
            )
            # Information-gated sequential test: both arms need observed post-candidate
            # evidence. This is not a fixed sample-count promotion rule.
            if candidate_exposures == 0 or incumbent_exposures == 0:
                continue
            if probability >= 1.0 - self.accept_risk:
                connection.execute(
                    "UPDATE harness_components SET status='retired',updated_at=? WHERE component_id=?",
                    (now, parent.component_id),
                )
                connection.execute(
                    "UPDATE harness_components SET status='active',updated_at=? WHERE component_id=?",
                    (now, candidate.component_id),
                )
                transitions.append(
                    {
                        "kind": candidate.kind,
                        "component_id": candidate.component_id,
                        "parent_id": parent.component_id,
                        "transition": "promoted",
                        "probability_superior": round(probability, 4),
                        "candidate_exposures": candidate_exposures,
                        "incumbent_exposures": incumbent_exposures,
                        "hypothesis": candidate.hypothesis,
                    }
                )
            elif probability <= self.accept_risk:
                connection.execute(
                    "UPDATE harness_components SET status='retired',updated_at=? WHERE component_id=?",
                    (now, candidate.component_id),
                )
                transitions.append(
                    {
                        "kind": candidate.kind,
                        "component_id": candidate.component_id,
                        "parent_id": parent.component_id,
                        "transition": "rejected",
                        "probability_superior": round(probability, 4),
                        "candidate_exposures": candidate_exposures,
                        "incumbent_exposures": incumbent_exposures,
                        "hypothesis": candidate.hypothesis,
                    }
                )
        return transitions

    def record_outcome(
        self,
        domain: str,
        component_ids: Iterable[str],
        *,
        verifier_score: float,
        evidence_complete: bool,
        session_id: str | None,
        meta: dict[str, Any] | None = None,
        evidence_completeness: float | None = None,
    ) -> list[dict[str, Any]]:
        """Update selected components with a verifier-grounded fractional Beta outcome."""
        q = max(0.0, min(1.0, float(verifier_score)))
        # Existing callers provide only the boolean closure signal. In that compatibility
        # path the verifier score itself is the conservative fractional completeness proxy
        # while a closed evidence set receives completeness 1.0.
        completeness = (
            max(0.0, min(1.0, float(evidence_completeness)))
            if evidence_completeness is not None
            else (1.0 if evidence_complete else q)
        )
        reward = self.verifier_potential(q, completeness)
        now = time.time()
        ids = list(dict.fromkeys(str(value) for value in component_ids if str(value)))
        outcome_meta = {
            **(meta or {}),
            "raw_verifier_score": q,
            "evidence_completeness": completeness,
            "reward": reward,
            "reward_method": "verifier_harmonic_potential",
        }
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for component_id in ids:
                row = connection.execute(
                    "SELECT domain FROM harness_components WHERE component_id=?",
                    (component_id,),
                ).fetchone()
                if not row or str(row["domain"]) != domain:
                    continue
                connection.execute(
                    "UPDATE harness_components SET alpha=alpha+?,beta=beta+?,uses=uses+1,updated_at=? WHERE component_id=?",
                    (reward, 1.0 - reward, now, component_id),
                )
                # Keep the historical column name for schema compatibility. It now stores
                # the verifier-grounded reward; raw verifier score is retained in meta_json.
                connection.execute(
                    "INSERT INTO harness_component_outcomes(component_id,session_id,verifier_score,evidence_complete,meta_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        component_id,
                        session_id,
                        reward,
                        int(bool(evidence_complete)),
                        json.dumps(outcome_meta, ensure_ascii=False, default=str),
                        now,
                    ),
                )
            return self._transition_shadows(connection, domain, now)

    def _has_shadow(self, connection: sqlite3.Connection, domain: str) -> bool:
        return bool(
            connection.execute(
                "SELECT 1 FROM harness_components WHERE domain=? AND status='shadow' LIMIT 1",
                (domain,),
            ).fetchone()
        )

    def _coordinate(
        self,
        connection: sqlite3.Connection,
        domain: str,
        *,
        reasoner_available: bool,
    ) -> str | None:
        """Select exactly one coordinate, favoring least-evolved/high-uncertainty state."""
        kinds = list(self.KINDS if reasoner_available else ("tool",))
        active = [self._active(connection, domain, kind) for kind in kinds]
        if not active:
            return None
        active.sort(key=lambda row: (row.generation, -row.variance, row.kind))
        return active[0].kind

    @staticmethod
    def _legal_tools(tool_catalog: list[dict[str, Any]]) -> set[str]:
        legal: set[str] = set()
        for row in tool_catalog:
            tool = str(row.get("tool") or "")
            mode = str(row.get("mode") or "read-only")
            if tool and mode in {"read-only", "mcp-read"} and not bool(row.get("requires_confirmation")):
                legal.add(tool)
        return legal

    @staticmethod
    def _dedupe(values: Iterable[Any], limit: int) -> list[str]:
        output: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in output:
                output.append(text)
            if len(output) >= limit:
                break
        return output

    def _apply_edits(
        self,
        kind: str,
        base: dict[str, Any],
        edits: list[dict[str, Any]],
        *,
        legal_tools: set[str],
    ) -> dict[str, Any] | None:
        allowed = self.FIELDS[kind]
        result = json.loads(json.dumps(base, ensure_ascii=False))
        changed = False
        for edit in edits[: self.edit_budget]:
            if not isinstance(edit, dict):
                continue
            operation = str(edit.get("op") or "").lower()
            field = str(edit.get("field") or "")
            if operation not in {"add", "delete", "replace"} or field not in allowed:
                continue
            value = edit.get("value")
            if field in {"preferred_tools", "avoid_tools"}:
                clean = [
                    item
                    for item in self._dedupe(value if isinstance(value, list) else [value], 12)
                    if item in legal_tools
                ]
                current = [item for item in self._dedupe(result.get(field, []), 12) if item in legal_tools]
                if operation == "add":
                    next_value = self._dedupe([*current, *clean], 12)
                elif operation == "delete":
                    remove = set(clean)
                    next_value = [item for item in current if item not in remove]
                else:
                    next_value = clean
            elif field in {"retrieval_terms", "roles"}:
                clean = self._dedupe(value if isinstance(value, list) else [value], 16)
                current = self._dedupe(result.get(field, []), 16)
                if operation == "add":
                    next_value = self._dedupe([*current, *clean], 16)
                elif operation == "delete":
                    remove = set(clean)
                    next_value = [item for item in current if item not in remove]
                else:
                    next_value = clean
            else:
                current = str(result.get(field) or "")
                text = str(value or "").strip()[:2400]
                if operation == "add":
                    next_value = (current + "\n" + text).strip()[:2400]
                elif operation == "delete":
                    next_value = "" if not text else current.replace(text, "").strip()
                else:
                    next_value = text
            if result.get(field) != next_value:
                result[field] = next_value
                changed = True
        return result if changed else None

    def _record_rejection(self, domain: str, kind: str, proposal: Any, reason: str) -> None:
        with self._lock, self._conn() as connection:
            connection.execute(
                "INSERT INTO harness_rejected_edits(domain,kind,proposal_json,reason,created_at) VALUES(?,?,?,?,?)",
                (
                    domain,
                    kind,
                    json.dumps(proposal, ensure_ascii=False, default=str)[:12000],
                    str(reason)[:500],
                    time.time(),
                ),
            )

    def _rejections(self, domain: str, kind: str, limit: int = 6) -> list[dict[str, Any]]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT proposal_json,reason FROM harness_rejected_edits WHERE domain=? AND kind=? ORDER BY id DESC LIMIT ?",
                (domain, kind, max(1, int(limit))),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            try:
                proposal: Any = json.loads(row["proposal_json"])
            except Exception:
                proposal = row["proposal_json"]
            output.append({"proposal": proposal, "reason": row["reason"]})
        return output

    def _record_replay_case(self, domain: str, trajectory: dict[str, Any]) -> None:
        with self._lock, self._conn() as connection:
            connection.execute(
                "INSERT INTO harness_replay_cases(domain,trajectory_json,created_at) VALUES(?,?,?)",
                (domain, json.dumps(trajectory, ensure_ascii=False, default=str)[:24000], time.time()),
            )

    def _replay_cases(self, domain: str, limit: int = 24) -> list[dict[str, Any]]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT trajectory_json FROM harness_replay_cases WHERE domain=? ORDER BY id DESC LIMIT ?",
                (domain, max(1, int(limit))),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                value = json.loads(row["trajectory_json"])
            except Exception:
                continue
            if isinstance(value, dict):
                output.append(value)
        return output

    @staticmethod
    def _json_payload(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except Exception:
            left, right = raw.find("{"), raw.rfind("}")
            if left >= 0 and right > left:
                try:
                    value = json.loads(raw[left : right + 1])
                    return value if isinstance(value, dict) else None
                except Exception:
                    return None
        return None

    def _tool_candidate(
        self,
        base: HarnessComponent,
        trajectory: dict[str, Any],
        tool_catalog: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str] | None:
        """Derive a tool-strategy mutation from current gaps and live tool metadata.

        There is no per-business tool hint table here. The candidate is induced from the
        current verifier gap text and the registered tool descriptions/evidence tags.
        """
        target_terms = set(
            _query_terms(
                " ".join(
                    [
                        str(trajectory.get("goal") or ""),
                        *[str(value) for value in (trajectory.get("missing") or [])],
                    ]
                ),
                limit=64,
            )
        )
        if not target_terms:
            return None
        legal = self._legal_tools(tool_catalog)
        scored: list[tuple[float, str]] = []
        for row in tool_catalog:
            tool = str(row.get("tool") or "")
            if tool not in legal:
                continue
            meta_terms = set(
                _query_terms(
                    " ".join(
                        [
                            str(row.get("purpose") or ""),
                            *[str(value) for value in (row.get("evidence_tags") or [])],
                        ]
                    ),
                    limit=64,
                )
            )
            union = target_terms | meta_terms
            similarity = len(target_terms & meta_terms) / max(1, len(union))
            if similarity > 0.0:
                scored.append((similarity, tool))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        preferred = self._dedupe([tool for _, tool in scored], 6)
        if not preferred or preferred == self._dedupe(base.content.get("preferred_tools", []), 6):
            return None
        content = dict(base.content)
        content["preferred_tools"] = preferred
        return content, "induce read-only tool strategy from verifier gaps and registered tool metadata"

    async def propose(
        self,
        domain: str,
        *,
        trajectory: dict[str, Any],
        tool_catalog: list[dict[str, Any]],
        reasoner=None,
    ) -> dict[str, Any] | None:
        """Generate at most one task-agnostic, rollback-safe shadow coordinate."""
        self._record_replay_case(domain, trajectory)
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_domain(connection, domain)
            if self._has_shadow(connection, domain):
                return None
            kind = self._coordinate(connection, domain, reasoner_available=reasoner is not None)
            if kind is None:
                return None
            base = self._active(connection, domain, kind)

        legal_tools = self._legal_tools(tool_catalog)
        candidate_content: dict[str, Any] | None = None
        hypothesis = ""
        proposal: dict[str, Any] | None = None

        if reasoner is not None:
            schema = {
                "kind": kind,
                "hypothesis": "a falsifiable, task-agnostic improvement hypothesis",
                "edits": [
                    {
                        "op": "add|delete|replace",
                        "field": "one allowed field for this coordinate",
                        "value": "string or string list",
                    }
                ],
            }
            prompt = (
                "You optimize exactly one cognitive component of the EcomEvo agent harness. "
                "Do not change model weights. Never modify Registry, Sandbox, Verifier, RBAC, "
                "approval policy, evidence admissibility, credentials, budgets, or business-action authority. "
                "The edit must be task-agnostic, transferable, inspectable and rollback-safe. "
                "Use the recent trajectory only as evidence for a general harness improvement. Return JSON only.\n"
                f"Coordinate: {kind}\n"
                f"Allowed fields: {sorted(self.FIELDS[kind])}\n"
                f"Current component: {json.dumps(base.content, ensure_ascii=False)}\n"
                f"Registered read-only tools: {json.dumps([row for row in tool_catalog if str(row.get('tool') or '') in legal_tools], ensure_ascii=False, default=str)[:8000]}\n"
                f"Trajectory digest: {json.dumps(trajectory, ensure_ascii=False, default=str)[:9000]}\n"
                f"Rejected nearby edits: {json.dumps(self._rejections(domain, kind), ensure_ascii=False, default=str)[:5000]}\n"
                f"Schema: {json.dumps(schema, ensure_ascii=False)}"
            )
            try:
                raw = await reasoner.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": "Propose one bounded, verifier-testable harness-coordinate edit; do not expose hidden reasoning.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    assets=[],
                    max_tokens=1200,
                    temperature=0.0,
                )
                proposal = self._json_payload(raw)
            except Exception as exc:
                self._record_rejection(domain, kind, {"error": type(exc).__name__}, "optimizer_model_error")
                proposal = None
            if proposal and str(proposal.get("kind") or "") == kind:
                candidate_content = self._apply_edits(
                    kind,
                    base.content,
                    list(proposal.get("edits") or []),
                    legal_tools=legal_tools,
                )
                hypothesis = str(proposal.get("hypothesis") or "bounded component edit")[:800]
            elif proposal:
                self._record_rejection(domain, kind, proposal, "kind_mismatch_or_invalid_schema")
        elif kind == "tool":
            deterministic = self._tool_candidate(base, trajectory, tool_catalog)
            if deterministic:
                candidate_content, hypothesis = deterministic
                proposal = {
                    "kind": kind,
                    "source": "verifier_gap_plus_tool_metadata",
                    "content": candidate_content,
                }

        if not candidate_content or candidate_content == base.content:
            if proposal:
                self._record_rejection(domain, kind, proposal, "no_safe_effective_edit")
            return None

        replay = self.replay_gate.evaluate(
            kind=kind,
            base=base.content,
            candidate=candidate_content,
            cases=self._replay_cases(domain),
            tool_catalog=tool_catalog,
        )
        if not replay.passed:
            self._record_rejection(domain, kind, proposal or candidate_content, "sandbox_replay_regression_gate:" + ",".join(replay.failures))
            return None

        now = time.time()
        component_id = f"hc-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._has_shadow(connection, domain):
                return None
            current = self._active(connection, domain, kind)
            if current.component_id != base.component_id:
                return None
            connection.execute(
                "INSERT INTO harness_components VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    component_id,
                    domain,
                    kind,
                    "shadow",
                    base.component_id,
                    json.dumps(candidate_content, ensure_ascii=False),
                    hypothesis,
                    1.0,
                    1.0,
                    0,
                    base.generation + 1,
                    now,
                    now,
                ),
            )
        return {
            "component_id": component_id,
            "domain": domain,
            "kind": kind,
            "status": "shadow",
            "parent_id": base.component_id,
            "generation": base.generation + 1,
            "hypothesis": hypothesis,
            "content": candidate_content,
            "acceptance": {
                "method": "post-candidate-cohort-posterior-superiority",
                "risk": self.accept_risk,
                "shadow_allocation": "posterior_probability",
                "fixed_run_threshold": False,
                "sandbox_replay": replay.as_dict(),
            },
            "authority": "cognition-only",
        }

    def snapshot(self, domain: str) -> dict[str, Any]:
        with self._lock, self._conn() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_domain(connection, domain)
            rows = connection.execute(
                "SELECT * FROM harness_components WHERE domain=? ORDER BY kind,generation,created_at",
                (domain,),
            ).fetchall()
        return {
            "domain": domain,
            "accept_risk": self.accept_risk,
            "edit_budget": self.edit_budget,
            "optimizer": "verifier-grounded-block-coordinate",
            "reward": "verifier-harmonic-potential",
            "components": [self._decode(row).as_dict() for row in rows],
        }
