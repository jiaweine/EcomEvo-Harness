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


class HarnessEvolutionOptimizer:
    """Verifier-grounded, component-wise optimizer for the cognitive harness.

    The design adapts three 2026 ideas to EcomEvo's evidence-governed runtime:

    * SkillOpt: bounded text-space add/delete/replace edits with a rejected-edit buffer;
    * HarnessCompass/SBCO: optimize one harness coordinate at a time and accept it from
      verifier-grounded outcome evidence instead of joint unconstrained self-rewrites;
    * AHE: every editable component, candidate hypothesis, cohort assignment and outcome is
      durable and inspectable.

    Only cognitive components are editable. Registry, Sandbox, Verifier, RBAC and business
    action authority are intentionally absent from the component type system.
    """

    KINDS = ("prompt", "tool", "memory", "delegation")
    FIELDS = {
        "prompt": {"guidance"},
        "tool": {"preferred_tools", "avoid_tools"},
        "memory": {"retrieval_terms", "guidance"},
        "delegation": {"roles", "guidance"},
    }

    def __init__(self, db_path: str | Path):
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
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(
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

    def _ensure_domain(self, c: sqlite3.Connection, domain: str):
        now = time.time()
        for kind in self.KINDS:
            row = c.execute(
                "SELECT 1 FROM harness_components WHERE domain=? AND kind=? AND status='active' LIMIT 1",
                (domain, kind),
            ).fetchone()
            if row:
                continue
            c.execute(
                "INSERT INTO harness_components VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"hc-{uuid.uuid4().hex[:12]}", domain, kind, "active", None, "{}",
                    "bootstrap empty cognitive component", 1.0, 1.0, 0, 0, now, now,
                ),
            )

    def _rows(self, c: sqlite3.Connection, domain: str) -> list[HarnessComponent]:
        rows = c.execute(
            "SELECT * FROM harness_components WHERE domain=? AND status IN ('active','shadow') ORDER BY kind,status,updated_at DESC",
            (domain,),
        ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _stable_unit(value: str) -> float:
        raw = hashlib.sha256(value.encode("utf-8")).digest()[:8]
        return int.from_bytes(raw, "big") / float((1 << 64) - 1)

    @staticmethod
    def _superiority(candidate: HarnessComponent, incumbent: HarnessComponent) -> float:
        variance = max(1e-12, candidate.variance + incumbent.variance)
        z = (candidate.mean - incumbent.mean) / math.sqrt(variance)
        return max(0.0, min(1.0, NormalDist().cdf(z)))

    def profile(self, domain: str, *, session_key: str) -> dict[str, Any]:
        """Return one durable harness profile and deterministically assign shadow traffic.

        A shadow candidate receives traffic in proportion to its posterior probability of
        beating the incumbent. This avoids a hand-written rollout percentage while keeping
        cohort assignment reproducible from the session id.
        """
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._ensure_domain(c, domain)
            rows = self._rows(c, domain)
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
            shadow_probability = 0.0
            if shadow and shadow.parent_id == active.component_id:
                shadow_probability = self._superiority(shadow, active)
                if self._stable_unit(f"{session_key}|{shadow.component_id}") < shadow_probability:
                    chosen = shadow
            profile["component_ids"].append(chosen.component_id)
            profile["components"][kind] = {
                **chosen.content,
                "component_id": chosen.component_id,
                "status": chosen.status,
                "generation": chosen.generation,
                "posterior_mean": round(chosen.mean, 4),
                "shadow_probability": round(shadow_probability, 4),
            }
        return profile

    def _component(self, component_id: str) -> HarnessComponent | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM harness_components WHERE component_id=?", (component_id,)).fetchone()
        return self._decode(row) if row else None

    def _transition_shadows(self, c: sqlite3.Connection, domain: str, now: float) -> list[dict[str, Any]]:
        transitions: list[dict[str, Any]] = []
        shadows = c.execute(
            "SELECT * FROM harness_components WHERE domain=? AND status='shadow' ORDER BY updated_at",
            (domain,),
        ).fetchall()
        for raw in shadows:
            candidate = self._decode(raw)
            if not candidate.parent_id:
                continue
            parent_raw = c.execute(
                "SELECT * FROM harness_components WHERE component_id=? AND status='active'",
                (candidate.parent_id,),
            ).fetchone()
            if not parent_raw:
                continue
            parent = self._decode(parent_raw)
            probability = self._superiority(candidate, parent)
            if probability >= 1.0 - self.accept_risk:
                c.execute(
                    "UPDATE harness_components SET status='retired',updated_at=? WHERE component_id=?",
                    (now, parent.component_id),
                )
                c.execute(
                    "UPDATE harness_components SET status='active',updated_at=? WHERE component_id=?",
                    (now, candidate.component_id),
                )
                transitions.append({
                    "kind": candidate.kind,
                    "component_id": candidate.component_id,
                    "parent_id": parent.component_id,
                    "transition": "promoted",
                    "probability_superior": round(probability, 4),
                    "hypothesis": candidate.hypothesis,
                })
            elif probability <= self.accept_risk:
                c.execute(
                    "UPDATE harness_components SET status='retired',updated_at=? WHERE component_id=?",
                    (now, candidate.component_id),
                )
                transitions.append({
                    "kind": candidate.kind,
                    "component_id": candidate.component_id,
                    "parent_id": parent.component_id,
                    "transition": "rejected",
                    "probability_superior": round(probability, 4),
                    "hypothesis": candidate.hypothesis,
                })
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
    ) -> list[dict[str, Any]]:
        """Fractional Beta update from the deterministic verifier score.

        Safety/admissibility is deliberately excluded from this learned objective: unsafe
        actions never enter the optimizer's action space in the first place.
        """
        reward = max(0.0, min(1.0, float(verifier_score)))
        now = time.time()
        ids = list(dict.fromkeys(str(x) for x in component_ids if str(x)))
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for component_id in ids:
                row = c.execute(
                    "SELECT domain FROM harness_components WHERE component_id=?",
                    (component_id,),
                ).fetchone()
                if not row or str(row["domain"]) != domain:
                    continue
                c.execute(
                    "UPDATE harness_components SET alpha=alpha+?,beta=beta+?,uses=uses+1,updated_at=? WHERE component_id=?",
                    (reward, 1.0 - reward, now, component_id),
                )
                c.execute(
                    "INSERT INTO harness_component_outcomes(component_id,session_id,verifier_score,evidence_complete,meta_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        component_id, session_id, reward, int(bool(evidence_complete)),
                        json.dumps(meta or {}, ensure_ascii=False, default=str), now,
                    ),
                )
            return self._transition_shadows(c, domain, now)

    def _active(self, c: sqlite3.Connection, domain: str, kind: str) -> HarnessComponent:
        self._ensure_domain(c, domain)
        row = c.execute(
            "SELECT * FROM harness_components WHERE domain=? AND kind=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
            (domain, kind),
        ).fetchone()
        assert row is not None
        return self._decode(row)

    def _has_shadow(self, c: sqlite3.Connection, domain: str) -> bool:
        return bool(c.execute(
            "SELECT 1 FROM harness_components WHERE domain=? AND status='shadow' LIMIT 1",
            (domain,),
        ).fetchone())

    def _coordinate(self, c: sqlite3.Connection, domain: str, *, reasoner_available: bool) -> str | None:
        """Choose one block only, preferring the least-evolved/most-uncertain coordinate."""
        kinds = list(self.KINDS if reasoner_available else ("tool",))
        active = [self._active(c, domain, kind) for kind in kinds]
        if not active:
            return None
        active.sort(key=lambda row: (row.generation, -row.variance, row.kind))
        return active[0].kind

    @staticmethod
    def _legal_tools(tool_catalog: list[dict[str, Any]]) -> set[str]:
        legal = set()
        for row in tool_catalog:
            tool = str(row.get("tool") or "")
            mode = str(row.get("mode") or "read-only")
            if tool and mode in {"read-only", "mcp-read"} and not bool(row.get("requires_confirmation")):
                legal.add(tool)
        return legal

    @staticmethod
    def _dedupe(values: Iterable[Any], limit: int) -> list[str]:
        out: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                break
        return out

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
            op = str(edit.get("op") or "").lower()
            field = str(edit.get("field") or "")
            if op not in {"add", "delete", "replace"} or field not in allowed:
                continue
            value = edit.get("value")
            if field in {"preferred_tools", "avoid_tools"}:
                clean = [x for x in self._dedupe(value if isinstance(value, list) else [value], 12) if x in legal_tools]
                current = [x for x in self._dedupe(result.get(field, []), 12) if x in legal_tools]
                if op == "add":
                    next_value = self._dedupe([*current, *clean], 12)
                elif op == "delete":
                    remove = set(clean)
                    next_value = [x for x in current if x not in remove]
                else:
                    next_value = clean
            elif field in {"retrieval_terms", "roles"}:
                clean = self._dedupe(value if isinstance(value, list) else [value], 16)
                current = self._dedupe(result.get(field, []), 16)
                if op == "add":
                    next_value = self._dedupe([*current, *clean], 16)
                elif op == "delete":
                    remove = set(clean)
                    next_value = [x for x in current if x not in remove]
                else:
                    next_value = clean
            else:
                current = str(result.get(field) or "")
                text = str(value or "").strip()[:2400]
                if op == "add":
                    next_value = (current + "\n" + text).strip()[:2400]
                elif op == "delete":
                    next_value = "" if not text else current.replace(text, "").strip()
                else:
                    next_value = text
            if result.get(field) != next_value:
                result[field] = next_value
                changed = True
        return result if changed else None

    def _record_rejection(self, domain: str, kind: str, proposal: Any, reason: str):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO harness_rejected_edits(domain,kind,proposal_json,reason,created_at) VALUES(?,?,?,?,?)",
                (domain, kind, json.dumps(proposal, ensure_ascii=False, default=str)[:12000], str(reason)[:500], time.time()),
            )

    def _rejections(self, domain: str, kind: str, limit: int = 6) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT proposal_json,reason FROM harness_rejected_edits WHERE domain=? AND kind=? ORDER BY id DESC LIMIT ?",
                (domain, kind, max(1, int(limit))),
            ).fetchall()
        out = []
        for row in rows:
            try:
                proposal = json.loads(row["proposal_json"])
            except Exception:
                proposal = row["proposal_json"]
            out.append({"proposal": proposal, "reason": row["reason"]})
        return out

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
                    value = json.loads(raw[left:right + 1])
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
        target_terms = set(_query_terms(" ".join([
            str(trajectory.get("goal") or ""),
            *[str(x) for x in (trajectory.get("missing") or [])],
        ]), limit=64))
        if not target_terms:
            return None
        scored: list[tuple[float, str]] = []
        legal = self._legal_tools(tool_catalog)
        for row in tool_catalog:
            tool = str(row.get("tool") or "")
            if tool not in legal:
                continue
            meta_terms = set(_query_terms(" ".join([
                str(row.get("purpose") or ""),
                *[str(x) for x in (row.get("evidence_tags") or [])],
            ]), limit=64))
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
        return content, "derive read-only tool preference from verifier gaps and current tool metadata"

    async def propose(
        self,
        domain: str,
        *,
        trajectory: dict[str, Any],
        tool_catalog: list[dict[str, Any]],
        reasoner=None,
    ) -> dict[str, Any] | None:
        """Create at most one shadow component for the domain.

        No new candidate is created while a previous coordinate is under shadow validation.
        This is the block-coordinate constraint that avoids multi-component interference.
        """
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._ensure_domain(c, domain)
            if self._has_shadow(c, domain):
                return None
            kind = self._coordinate(c, domain, reasoner_available=reasoner is not None)
            if kind is None:
                return None
            base = self._active(c, domain, kind)

        legal_tools = self._legal_tools(tool_catalog)
        candidate_content: dict[str, Any] | None = None
        hypothesis = ""
        proposal: dict[str, Any] | None = None

        if reasoner is not None:
            schema = {
                "kind": kind,
                "hypothesis": "可由后续 verifier outcome 证伪的改进假设",
                "edits": [
                    {"op": "add|delete|replace", "field": "该 kind 的允许字段", "value": "字符串或字符串列表"}
                ],
            }
            prompt = (
                "你是 EcomEvo 的 harness optimizer。只优化一个认知组件，不改模型权重。"
                "绝不能修改 Registry、Sandbox、Verifier、RBAC、审批规则或真实业务动作权限。"
                "编辑必须任务无关、可迁移、可回滚；只返回 JSON，不输出隐藏思维。\n"
                f"组件：{kind}\n允许字段：{sorted(self.FIELDS[kind])}\n"
                f"当前组件：{json.dumps(base.content, ensure_ascii=False)}\n"
                f"当前只读工具：{json.dumps([row for row in tool_catalog if str(row.get('tool') or '') in legal_tools], ensure_ascii=False, default=str)[:8000]}\n"
                f"最近轨迹摘要：{json.dumps(trajectory, ensure_ascii=False, default=str)[:9000]}\n"
                f"被拒绝的近似编辑：{json.dumps(self._rejections(domain, kind), ensure_ascii=False, default=str)[:5000]}\n"
                f"返回结构：{json.dumps(schema, ensure_ascii=False)}"
            )
            try:
                raw = await reasoner.chat(
                    messages=[
                        {"role": "system", "content": "提出受约束、可验证、可回滚的 harness 单坐标编辑。"},
                        {"role": "user", "content": prompt},
                    ],
                    assets=[], max_tokens=1200, temperature=0.0,
                )
                proposal = self._json_payload(raw)
            except Exception as exc:
                self._record_rejection(domain, kind, {"error": type(exc).__name__}, "optimizer_model_error")
                proposal = None
            if proposal and str(proposal.get("kind") or "") == kind:
                candidate_content = self._apply_edits(
                    kind, base.content, list(proposal.get("edits") or []), legal_tools=legal_tools,
                )
                hypothesis = str(proposal.get("hypothesis") or "bounded component edit")[:800]
            elif proposal:
                self._record_rejection(domain, kind, proposal, "kind_mismatch_or_invalid_schema")
        elif kind == "tool":
            deterministic = self._tool_candidate(base, trajectory, tool_catalog)
            if deterministic:
                candidate_content, hypothesis = deterministic
                proposal = {"kind": kind, "source": "verifier_gap_tool_metadata", "content": candidate_content}

        if not candidate_content or candidate_content == base.content:
            if proposal:
                self._record_rejection(domain, kind, proposal, "no_safe_effective_edit")
            return None

        now = time.time()
        component_id = f"hc-{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if self._has_shadow(c, domain):
                return None
            current = self._active(c, domain, kind)
            if current.component_id != base.component_id:
                return None
            c.execute(
                "INSERT INTO harness_components VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    component_id, domain, kind, "shadow", base.component_id,
                    json.dumps(candidate_content, ensure_ascii=False), hypothesis,
                    1.0, 1.0, 0, base.generation + 1, now, now,
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
                "method": "posterior_superiority",
                "risk": self.accept_risk,
                "shadow_allocation": "posterior_probability",
            },
            "authority": "cognition-only",
        }

    def snapshot(self, domain: str) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._ensure_domain(c, domain)
            rows = c.execute(
                "SELECT * FROM harness_components WHERE domain=? ORDER BY kind,generation,created_at",
                (domain,),
            ).fetchall()
        return {
            "domain": domain,
            "accept_risk": self.accept_risk,
            "edit_budget": self.edit_budget,
            "components": [self._decode(row).as_dict() for row in rows],
        }
