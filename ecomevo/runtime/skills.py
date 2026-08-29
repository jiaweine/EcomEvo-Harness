from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class RuntimeSkill:
    skill_id: str
    domain: str
    niche: str
    name: str
    guidance: str
    preferred_tools: list[str] = field(default_factory=list)
    trigger_terms: list[str] = field(default_factory=list)
    status: str = "shadow"
    shadow_score: float = 0.0
    alpha: float = 2.0
    beta: float = 2.0
    uses: int = 0
    wins: int = 0
    losses: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    source_patch_id: str | None = None

    @property
    def posterior_mean(self) -> float:
        return float(self.alpha) / max(1e-9, float(self.alpha) + float(self.beta))

    @property
    def posterior_lower_bound(self) -> float:
        mean = self.posterior_mean
        n = max(1.0, float(self.alpha) + float(self.beta))
        variance = mean * (1.0 - mean) / (n + 1.0)
        return max(0.0, mean - 1.64 * math.sqrt(max(0.0, variance)))

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["posterior_mean"] = round(self.posterior_mean, 4)
        row["posterior_lower_bound"] = round(self.posterior_lower_bound, 4)
        return row


class AdaptiveSkillLibrary:
    """Persistent, posterior-ranked runtime skills with quality-diversity niches.

    Skills can steer information gathering and delegation. They never become evidence and
    they never grant side-effect authority. Candidates first live in shadow, then deterministic
    replay gates can promote them. Live outcomes update a Beta posterior so weak skills decay.
    """

    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runtime_skills(
                    skill_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    niche TEXT NOT NULL,
                    name TEXT NOT NULL,
                    guidance TEXT NOT NULL,
                    preferred_tools_json TEXT NOT NULL,
                    trigger_terms_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    shadow_score REAL NOT NULL,
                    alpha REAL NOT NULL,
                    beta REAL NOT NULL,
                    uses INTEGER NOT NULL,
                    wins INTEGER NOT NULL,
                    losses INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    source_patch_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_skills_domain_status
                    ON runtime_skills(domain,status,updated_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_skills_niche
                    ON runtime_skills(domain,niche,status);
                CREATE TABLE IF NOT EXISTS evolution_policy(
                    domain TEXT PRIMARY KEY,
                    promotion_threshold REAL NOT NULL,
                    retirement_threshold REAL NOT NULL,
                    exploration REAL NOT NULL,
                    updates INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skill_outcomes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id TEXT NOT NULL,
                    session_id TEXT,
                    success INTEGER NOT NULL,
                    score REAL NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )

    @staticmethod
    def niche_for(
        domain: str,
        trigger_terms: Iterable[str],
        preferred_tools: Iterable[str] = (),
    ) -> str:
        terms = sorted({str(x).strip().lower() for x in trigger_terms if str(x).strip()})[:10]
        tools = sorted({str(x).strip() for x in preferred_tools if str(x).strip()})[:6]
        raw = json.dumps([domain, terms, tools], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:18]

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any]) -> RuntimeSkill:
        d = dict(row)
        return RuntimeSkill(
            skill_id=d["skill_id"],
            domain=d["domain"],
            niche=d["niche"],
            name=d["name"],
            guidance=d["guidance"],
            preferred_tools=list(json.loads(d.get("preferred_tools_json") or "[]")),
            trigger_terms=list(json.loads(d.get("trigger_terms_json") or "[]")),
            status=d["status"],
            shadow_score=float(d["shadow_score"]),
            alpha=float(d["alpha"]),
            beta=float(d["beta"]),
            uses=int(d["uses"]),
            wins=int(d["wins"]),
            losses=int(d["losses"]),
            created_at=float(d["created_at"]),
            updated_at=float(d["updated_at"]),
            source_patch_id=d.get("source_patch_id"),
        )

    def get(self, skill_id: str) -> RuntimeSkill | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM runtime_skills WHERE skill_id=?", (skill_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list(
        self,
        *,
        domain: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSkill]:
        q = "SELECT * FROM runtime_skills WHERE 1=1"
        args: list[Any] = []
        if domain is not None:
            q += " AND domain=?"
            args.append(domain)
        if status is not None:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [self._decode(r) for r in rows]

    def relevant(
        self,
        domain: str,
        *,
        query: str = "",
        missing: Iterable[str] = (),
        limit: int = 6,
    ) -> list[RuntimeSkill]:
        candidates = self.list(domain=domain, status="active", limit=100)
        haystack = (str(query) + " " + " ".join(str(x) for x in missing)).lower()
        scored: list[tuple[float, RuntimeSkill]] = []
        for skill in candidates:
            term_hits = sum(1 for t in skill.trigger_terms if t and t.lower() in haystack)
            score = (
                0.46 * skill.posterior_mean
                + 0.34 * skill.shadow_score
                + min(0.20, 0.05 * term_hits)
            )
            scored.append((score, skill))
        scored.sort(key=lambda x: (x[0], x[1].updated_at), reverse=True)
        return [s for _, s in scored[: max(1, int(limit))]]

    def policy(self, domain: str) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM evolution_policy WHERE domain=?", (domain,)
            ).fetchone()
        if row is None:
            now = time.time()
            with self._lock, self._conn() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT OR IGNORE INTO evolution_policy VALUES(?,?,?,?,?,?)",
                    (domain, 0.92, 0.45, 0.60, 0, now),
                )
                row = c.execute(
                    "SELECT * FROM evolution_policy WHERE domain=?", (domain,)
                ).fetchone()
        if row is None:  # pragma: no cover
            raise RuntimeError(f"failed to initialize evolution policy for {domain}")
        return {
            "domain": domain,
            "promotion_threshold": float(row["promotion_threshold"]),
            "retirement_threshold": float(row["retirement_threshold"]),
            "exploration": float(row["exploration"]),
            "updates": int(row["updates"]),
            "updated_at": float(row["updated_at"]),
        }

    @staticmethod
    def _policy_in_transaction(
        c: sqlite3.Connection,
        domain: str,
        *,
        now: float,
    ) -> dict[str, Any]:
        c.execute(
            "INSERT OR IGNORE INTO evolution_policy VALUES(?,?,?,?,?,?)",
            (domain, 0.92, 0.45, 0.60, 0, now),
        )
        row = c.execute(
            "SELECT promotion_threshold,retirement_threshold,exploration,updates,updated_at "
            "FROM evolution_policy WHERE domain=?",
            (domain,),
        ).fetchone()
        if row is None:  # pragma: no cover
            raise RuntimeError(f"failed to initialize evolution policy for {domain}")
        return {
            "domain": domain,
            "promotion_threshold": float(row["promotion_threshold"]),
            "retirement_threshold": float(row["retirement_threshold"]),
            "exploration": float(row["exploration"]),
            "updates": int(row["updates"]),
            "updated_at": float(row["updated_at"]),
        }

    @classmethod
    def _adapt_policy_in_transaction(
        cls,
        c: sqlite3.Connection,
        domain: str,
        *,
        success: bool,
        skill_used: bool,
        now: float,
    ) -> dict[str, Any]:
        current = cls._policy_in_transaction(c, domain, now=now)
        promotion = float(current["promotion_threshold"])
        retirement = float(current["retirement_threshold"])
        exploration = float(current["exploration"])
        if skill_used and not success:
            promotion = min(0.98, promotion + 0.008)
            exploration = min(0.90, exploration + 0.04)
            retirement = min(0.55, retirement + 0.005)
        elif skill_used and success:
            promotion = max(0.90, promotion - 0.003)
            exploration = max(0.25, exploration - 0.02)
            retirement = max(0.40, retirement - 0.002)
        elif not skill_used and not success:
            exploration = min(0.90, exploration + 0.025)
        c.execute(
            "UPDATE evolution_policy SET promotion_threshold=?,retirement_threshold=?,"
            "exploration=?,updates=updates+1,updated_at=? WHERE domain=?",
            (promotion, retirement, exploration, now, domain),
        )
        return {
            "domain": domain,
            "promotion_threshold": promotion,
            "retirement_threshold": retirement,
            "exploration": exploration,
            "updates": int(current["updates"]) + 1,
            "updated_at": now,
        }

    def _adapt_policy(self, domain: str, *, success: bool, skill_used: bool):
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._adapt_policy_in_transaction(
                c,
                domain,
                success=success,
                skill_used=skill_used,
                now=time.time(),
            )

    def note_run(self, domain: str, *, success: bool, skill_used: bool = False):
        self._adapt_policy(domain, success=success, skill_used=skill_used)

    def upsert_candidate(
        self,
        *,
        domain: str,
        name: str,
        guidance: str,
        preferred_tools: list[str],
        trigger_terms: list[str],
        shadow_score: float,
        source_patch_id: str | None = None,
        promote: bool = False,
    ) -> RuntimeSkill:
        now = time.time()
        preferred_tools = list(
            dict.fromkeys(str(x).strip() for x in preferred_tools if str(x).strip())
        )[:8]
        trigger_terms = list(
            dict.fromkeys(str(x).strip() for x in trigger_terms if str(x).strip())
        )[:12]
        niche = self.niche_for(domain, trigger_terms, preferred_tools)
        status = "active" if promote else "shadow"
        seed_strength = 4.0
        alpha = 1.5 + seed_strength * max(0.0, min(1.0, shadow_score))
        beta = 1.5 + seed_strength * (1.0 - max(0.0, min(1.0, shadow_score)))
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            current = c.execute(
                "SELECT * FROM runtime_skills WHERE domain=? AND niche=? "
                "AND status!='retired' ORDER BY shadow_score DESC,updated_at DESC LIMIT 1",
                (domain, niche),
            ).fetchone()
            if current:
                existing = self._decode(current)
                incumbent_rank = 0.55 * existing.shadow_score + 0.45 * existing.posterior_mean
                candidate_rank = 0.55 * float(shadow_score) + 0.45 * (alpha / (alpha + beta))
                if candidate_rank <= incumbent_rank + 0.015:
                    return existing
                c.execute(
                    "UPDATE runtime_skills SET status='retired',updated_at=? WHERE skill_id=?",
                    (now, existing.skill_id),
                )
            skill_id = f"skill-{uuid.uuid4().hex[:12]}"
            c.execute(
                "INSERT INTO runtime_skills VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    skill_id,
                    domain,
                    niche,
                    str(name)[:120],
                    str(guidance)[:3000],
                    json.dumps(preferred_tools, ensure_ascii=False),
                    json.dumps(trigger_terms, ensure_ascii=False),
                    status,
                    float(shadow_score),
                    alpha,
                    beta,
                    0,
                    0,
                    0,
                    now,
                    now,
                    source_patch_id,
                ),
            )
        return self.get(skill_id)  # type: ignore[return-value]

    def promote(self, skill_id: str) -> RuntimeSkill | None:
        skill = self.get(skill_id)
        if not skill:
            return None
        threshold = float(self.policy(skill.domain)["promotion_threshold"])
        if skill.shadow_score < threshold:
            return skill
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE runtime_skills SET status='active',updated_at=? WHERE skill_id=?",
                (now, skill_id),
            )
        return self.get(skill_id)

    def record_outcome(
        self,
        skill_ids: Iterable[str],
        *,
        success: bool,
        score: float,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[RuntimeSkill]:
        ids = list(dict.fromkeys(str(x) for x in skill_ids if str(x)))
        if not ids:
            return []

        now = time.time()
        context_json = json.dumps(context or {}, ensure_ascii=False, default=str)
        out: list[RuntimeSkill] = []
        with self._lock, self._conn() as c:
            # One learning round is one atomic writer transaction. The previous
            # implementation opened a connection + BEGIN IMMEDIATE per skill and then
            # another transaction per touched domain for policy adaptation.
            c.execute("BEGIN IMMEDIATE")
            skills: list[RuntimeSkill] = []
            for skill_id in ids:
                row = c.execute(
                    "SELECT * FROM runtime_skills WHERE skill_id=?", (skill_id,)
                ).fetchone()
                if row is not None:
                    skills.append(self._decode(row))
            if not skills:
                return []

            policies: dict[str, dict[str, Any]] = {}
            for skill in skills:
                if skill.domain not in policies:
                    policies[skill.domain] = self._policy_in_transaction(
                        c, skill.domain, now=now
                    )

            for skill in skills:
                policy = policies[skill.domain]
                alpha = skill.alpha + (1.0 if success else 0.0)
                beta = skill.beta + (0.0 if success else 1.0)
                uses = skill.uses + 1
                wins = skill.wins + int(success)
                losses = skill.losses + int(not success)
                mean = alpha / max(1e-9, alpha + beta)
                status = skill.status
                if (
                    status == "shadow"
                    and uses >= 2
                    and mean >= 0.68
                    and skill.shadow_score >= float(policy["promotion_threshold"])
                ):
                    status = "active"
                elif (
                    status == "active"
                    and uses >= 5
                    and mean < float(policy["retirement_threshold"])
                ):
                    status = "retired"
                c.execute(
                    "UPDATE runtime_skills SET alpha=?,beta=?,uses=?,wins=?,losses=?,"
                    "status=?,updated_at=? WHERE skill_id=?",
                    (alpha, beta, uses, wins, losses, status, now, skill.skill_id),
                )
                c.execute(
                    "INSERT INTO skill_outcomes"
                    "(skill_id,session_id,success,score,context_json,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        skill.skill_id,
                        session_id,
                        int(success),
                        float(score),
                        context_json,
                        now,
                    ),
                )
                out.append(
                    RuntimeSkill(
                        skill_id=skill.skill_id,
                        domain=skill.domain,
                        niche=skill.niche,
                        name=skill.name,
                        guidance=skill.guidance,
                        preferred_tools=list(skill.preferred_tools),
                        trigger_terms=list(skill.trigger_terms),
                        status=status,
                        shadow_score=skill.shadow_score,
                        alpha=alpha,
                        beta=beta,
                        uses=uses,
                        wins=wins,
                        losses=losses,
                        created_at=skill.created_at,
                        updated_at=now,
                        source_patch_id=skill.source_patch_id,
                    )
                )

            for domain in policies:
                self._adapt_policy_in_transaction(
                    c,
                    domain,
                    success=success,
                    skill_used=True,
                    now=now,
                )
        return out

    def snapshot(self, domain: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        return [x.as_dict() for x in self.list(domain=domain, limit=limit)]
