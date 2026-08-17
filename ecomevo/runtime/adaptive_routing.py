from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .control_policy import DecisionPolicy


class AdaptiveRoutingStore:
    """Persistent hierarchical Bayesian policy for read-only tool routing.

    Production guarantees:
    - bootstrap coefficients are priors, not permanent policy weights;
    - one immutable posterior snapshot is used for an entire routing round;
    - reliability reads are batched with the snapshot (no per-candidate N+1 query);
    - all credit from one round is persisted in one write transaction;
    - only cognition ranking is learnable; sandbox/verifier/authority remain external.
    """

    FEATURE_NAMES = (
        "bias",
        "coverage",
        "authority",
        "skill_support",
        "novelty",
        "contradiction",
        "specificity",
        "tool_reliability",
        "cost_pressure",
        "redundancy",
        "gap_pressure",
        "recovery_context",
    )
    PRIOR_MEAN = (0.02, 1.45, 0.55, 0.40, 0.25, 0.12, 0.08, 0.28, -0.45, -0.30, 0.08, 0.0)
    PRIOR_PRECISION = 3.0
    DECAY = 0.997
    DOMAIN_SHRINKAGE = 24.0
    RELIABILITY_SHRINKAGE = 12.0
    SHADOW_MIN_SAMPLES = 12
    MAX_ACTIVATION = 0.96

    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    @property
    def dim(self) -> int:
        return len(self.FEATURE_NAMES)

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS routing_policy(
                    policy_key TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    a_json TEXT NOT NULL,
                    b_json TEXT NOT NULL,
                    samples INTEGER NOT NULL,
                    reward_ewma REAL NOT NULL,
                    residual_ewma REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_routing_policy_domain ON routing_policy(domain,scope);
                CREATE TABLE IF NOT EXISTS routing_outcomes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    reward REAL NOT NULL,
                    feature_json TEXT NOT NULL,
                    meta_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_routing_outcomes_domain_time
                    ON routing_outcomes(domain,created_at DESC);
                CREATE TABLE IF NOT EXISTS routing_tool_stats(
                    domain TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    alpha REAL NOT NULL,
                    beta REAL NOT NULL,
                    uses INTEGER NOT NULL,
                    reward_ewma REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(domain,tool)
                );
                """
            )

    @staticmethod
    def _key(domain: str, scope: str) -> str:
        return f"{scope}:{domain}"

    def _prior_a(self) -> list[list[float]]:
        return [[self.PRIOR_PRECISION if i == j else 0.0 for j in range(self.dim)] for i in range(self.dim)]

    def _prior_b(self) -> list[float]:
        return [self.PRIOR_PRECISION * value for value in self.PRIOR_MEAN]

    def _default_row(self, domain: str, scope: str) -> dict[str, Any]:
        return {
            "policy_key": self._key(domain, scope),
            "domain": domain,
            "scope": scope,
            "a": self._prior_a(),
            "b": self._prior_b(),
            "samples": 0,
            "reward_ewma": 0.0,
            "residual_ewma": 0.25,
            "updated_at": 0.0,
        }

    @staticmethod
    def _decode_row(row: sqlite3.Row | None, fallback: dict[str, Any]) -> dict[str, Any]:
        if row is None:
            return fallback
        data = dict(row)
        data["a"] = [[float(x) for x in values] for values in json.loads(data.pop("a_json"))]
        data["b"] = [float(x) for x in json.loads(data.pop("b_json"))]
        return data

    def _ensure(self, c: sqlite3.Connection, domain: str, scope: str):
        key = self._key(domain, scope)
        if c.execute("SELECT 1 FROM routing_policy WHERE policy_key=?", (key,)).fetchone():
            return
        row = self._default_row(domain, scope)
        c.execute(
            "INSERT INTO routing_policy(policy_key,domain,scope,a_json,b_json,samples,reward_ewma,residual_ewma,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                key, domain, scope, json.dumps(row["a"]), json.dumps(row["b"]),
                0, 0.0, 0.25, time.time(),
            ),
        )

    @staticmethod
    def _inverse(matrix: list[list[float]]) -> list[list[float]]:
        n = len(matrix)
        aug = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
        for col in range(n):
            pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
            if abs(aug[pivot][col]) < 1e-10:
                aug[col][col] += 1e-6
                pivot = col
            if pivot != col:
                aug[col], aug[pivot] = aug[pivot], aug[col]
            scale = aug[col][col]
            aug[col] = [value / scale for value in aug[col]]
            for row in range(n):
                if row == col:
                    continue
                factor = aug[row][col]
                if abs(factor) < 1e-14:
                    continue
                aug[row] = [left - factor * right for left, right in zip(aug[row], aug[col])]
        return [row[n:] for row in aug]

    @staticmethod
    def _matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
        return [sum(a * b for a, b in zip(row, vector)) for row in matrix]

    @staticmethod
    def _dot(left: Iterable[float], right: Iterable[float]) -> float:
        return sum(float(a) * float(b) for a, b in zip(left, right))

    def _posterior_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        inverse = self._inverse(row["a"])
        mean = self._matvec(inverse, row["b"])
        return {**row, "inverse": inverse, "mean": mean}

    @staticmethod
    def _beta_mean(row: sqlite3.Row | dict[str, Any] | None) -> float:
        if not row:
            return 0.5
        alpha = float(row["alpha"])
        beta = float(row["beta"])
        return alpha / max(1e-9, alpha + beta)

    def _reliability_map(self, c: sqlite3.Connection, domain: str, tools: list[str]) -> dict[str, float]:
        tools = list(dict.fromkeys(str(tool) for tool in tools if str(tool)))
        if not tools:
            return {}
        placeholders = ",".join("?" for _ in tools)
        rows = c.execute(
            f"SELECT domain,tool,alpha,beta,uses FROM routing_tool_stats WHERE domain IN (?,?) AND tool IN ({placeholders})",
            ["*", domain, *tools],
        ).fetchall()
        indexed = {(str(row["domain"]), str(row["tool"])): row for row in rows}
        result: dict[str, float] = {}
        for tool in tools:
            global_row = indexed.get(("*", tool))
            local_row = indexed.get((domain, tool))
            global_mean = self._beta_mean(global_row)
            local_mean = self._beta_mean(local_row)
            local_uses = int(local_row["uses"]) if local_row else 0
            tau = local_uses / (local_uses + self.RELIABILITY_SHRINKAGE)
            result[tool] = (1.0 - tau) * global_mean + tau * local_mean
        return result

    def prepare_context(self, domain: str, *, tools: list[str], exploration: float) -> dict[str, Any]:
        started = time.perf_counter()
        global_key = self._key("*", "global")
        domain_key = self._key(domain, "domain")
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM routing_policy WHERE policy_key IN (?,?)",
                (global_key, domain_key),
            ).fetchall()
            indexed = {str(row["policy_key"]): row for row in rows}
            reliability = self._reliability_map(c, domain, tools)

        global_p = self._posterior_from_row(
            self._decode_row(indexed.get(global_key), self._default_row("*", "global"))
        )
        domain_p = self._posterior_from_row(
            self._decode_row(indexed.get(domain_key), self._default_row(domain, "domain"))
        )
        n = int(domain_p["samples"])
        global_n = int(global_p["samples"])
        tau = n / (n + self.DOMAIN_SHRINKAGE)
        residual = max(float(global_p["residual_ewma"]), float(domain_p["residual_ewma"]))
        confidence = max(0.30, min(1.0, 1.0 / (1.0 + 1.8 * residual)))
        if n < self.SHADOW_MIN_SAMPLES:
            if global_n >= 48:
                activation = min(0.18, (global_n / (global_n + 180.0)) * confidence)
                mode = "global_transfer"
            else:
                activation = 0.0
                mode = "shadow"
        else:
            sample_factor = (n - self.SHADOW_MIN_SAMPLES + 1.0) / (n + 28.0)
            activation = min(self.MAX_ACTIVATION, sample_factor * confidence)
            mode = "adaptive"
        explore = max(0.0, min(1.0, float(exploration)))
        beta = (0.12 + 0.42 * explore) * (1.0 + min(1.0, residual))
        return {
            "global": global_p,
            "domain": domain_p,
            "tau": tau,
            "residual": residual,
            "activation": activation,
            "mode": mode,
            "beta": beta,
            "samples": n,
            "global_samples": global_n,
            "reliability": reliability,
            "prepare_ms": (time.perf_counter() - started) * 1000.0,
        }

    def score_prepared(self, vector: list[float], prepared: dict[str, Any]) -> dict[str, float | int | str]:
        if len(vector) != self.dim:
            raise ValueError(f"routing feature dimension mismatch: {len(vector)} != {self.dim}")
        global_p = prepared["global"]
        domain_p = prepared["domain"]
        tau = float(prepared["tau"])
        n = int(prepared["samples"])
        global_mean = self._dot(global_p["mean"], vector)
        domain_mean = self._dot(domain_p["mean"], vector)
        posterior_mean = (1.0 - tau) * global_mean + tau * domain_mean
        global_v = self._matvec(global_p["inverse"], vector)
        domain_v = self._matvec(domain_p["inverse"], vector)
        global_var = max(0.0, self._dot(vector, global_v))
        domain_var = max(0.0, self._dot(vector, domain_v))
        uncertainty = math.sqrt(max(0.0, (1.0 - tau) ** 2 * global_var + tau ** 2 * domain_var))
        prior_score = self._dot(self.PRIOR_MEAN, vector)
        optimistic = posterior_mean + float(prepared["beta"]) * uncertainty
        if n < 64:
            optimistic = max(prior_score - 0.70, min(prior_score + 0.70, optimistic))
        activation = float(prepared["activation"])
        final = (1.0 - activation) * prior_score + activation * optimistic
        return {
            "score": float(final),
            "prior": float(prior_score),
            "posterior": float(posterior_mean),
            "uncertainty": float(uncertainty),
            "activation": activation,
            "samples": n,
            "mode": str(prepared["mode"]),
            "residual": float(prepared["residual"]),
        }

    def abstain_vector(self) -> list[float]:
        values = [0.0] * self.dim
        values[0] = 1.0
        return values

    def _update_policy_batch(
        self,
        c: sqlite3.Connection,
        domain: str,
        scope: str,
        rows: list[dict[str, Any]],
        now: float,
    ) -> dict[str, Any]:
        self._ensure(c, domain, scope)
        key = self._key(domain, scope)
        raw = c.execute("SELECT * FROM routing_policy WHERE policy_key=?", (key,)).fetchone()
        state = self._decode_row(raw, self._default_row(domain, scope))
        a = state["a"]
        b = state["b"]
        prior_a = self._prior_a()
        prior_b = self._prior_b()

        # Forget once per observed round, not once per tool in the same parallel batch.
        for i in range(self.dim):
            for j in range(self.dim):
                a[i][j] = prior_a[i][j] + self.DECAY * (a[i][j] - prior_a[i][j])
            b[i] = prior_b[i] + self.DECAY * (b[i] - prior_b[i])

        mean_before = self._matvec(self._inverse(a), b)
        reward_ewma = float(state["reward_ewma"])
        residual_ewma = float(state["residual_ewma"])
        for item in rows:
            vector = item["vector"]
            reward = item["reward"]
            residual = abs(reward - self._dot(mean_before, vector))
            for i, xi in enumerate(vector):
                b[i] += reward * xi
                for j, xj in enumerate(vector):
                    a[i][j] += xi * xj
            reward_ewma = 0.92 * reward_ewma + 0.08 * reward
            residual_ewma = 0.90 * residual_ewma + 0.10 * residual

        c.execute(
            "UPDATE routing_policy SET a_json=?,b_json=?,samples=samples+?,reward_ewma=?,residual_ewma=?,updated_at=? WHERE policy_key=?",
            (
                json.dumps(a), json.dumps(b), len(rows), reward_ewma,
                residual_ewma, now, key,
            ),
        )
        return {
            **state,
            "a": a,
            "b": b,
            "samples": int(state["samples"]) + len(rows),
            "reward_ewma": reward_ewma,
            "residual_ewma": residual_ewma,
            "updated_at": now,
        }

    def _update_reliability_batch(
        self,
        c: sqlite3.Connection,
        domain: str,
        rows: list[dict[str, Any]],
        now: float,
    ):
        tools = list(dict.fromkeys(str(item["tool"]) for item in rows if str(item.get("tool") or "")))
        if not tools:
            return
        placeholders = ",".join("?" for _ in tools)
        existing_rows = c.execute(
            f"SELECT * FROM routing_tool_stats WHERE domain IN (?,?) AND tool IN ({placeholders})",
            ["*", domain, *tools],
        ).fetchall()
        state = {(str(row["domain"]), str(row["tool"])): dict(row) for row in existing_rows}
        for item in rows:
            tool = str(item["tool"])
            ok = bool(item.get("ok", False))
            reward = float(item["reward"])
            for scope_domain in ("*", domain):
                key = (scope_domain, tool)
                current = state.get(key)
                if current:
                    alpha = float(current["alpha"]) + (1.0 if ok else 0.0)
                    beta = float(current["beta"]) + (0.0 if ok else 1.0)
                    uses = int(current["uses"]) + 1
                    reward_ewma = 0.92 * float(current["reward_ewma"]) + 0.08 * reward
                    c.execute(
                        "UPDATE routing_tool_stats SET alpha=?,beta=?,uses=?,reward_ewma=?,updated_at=? WHERE domain=? AND tool=?",
                        (alpha, beta, uses, reward_ewma, now, scope_domain, tool),
                    )
                    state[key] = {
                        "domain": scope_domain,
                        "tool": tool,
                        "alpha": alpha,
                        "beta": beta,
                        "uses": uses,
                        "reward_ewma": reward_ewma,
                    }
                else:
                    alpha = 2.0 + (1.0 if ok else 0.0)
                    beta = 2.0 + (0.0 if ok else 1.0)
                    c.execute(
                        "INSERT INTO routing_tool_stats(domain,tool,alpha,beta,uses,reward_ewma,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (scope_domain, tool, alpha, beta, 1, reward, now),
                    )
                    state[key] = {
                        "domain": scope_domain,
                        "tool": tool,
                        "alpha": alpha,
                        "beta": beta,
                        "uses": 1,
                        "reward_ewma": reward,
                    }

    def apply_batch(
        self,
        domain: str,
        *,
        phase: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        clean = []
        for item in rows:
            vector = [float(value) for value in (item.get("vector") or [])]
            tool = str(item.get("tool") or "")[:120]
            if len(vector) != self.dim or not tool:
                continue
            reward = max(-1.0, min(1.0, float(item.get("reward", 0.0))))
            clean.append({
                "tool": tool,
                "vector": vector,
                "reward": reward,
                "ok": bool(item.get("ok", False)),
                "meta": item.get("meta") if isinstance(item.get("meta"), dict) else {},
            })
        if not clean:
            return None

        started = time.perf_counter()
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._update_policy_batch(c, "*", "global", clean, now)
            domain_state = self._update_policy_batch(c, domain, "domain", clean, now)
            c.executemany(
                "INSERT INTO routing_outcomes(domain,phase,tool,reward,feature_json,meta_json,created_at) VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        domain, str(phase)[:40], item["tool"], item["reward"],
                        json.dumps(item["vector"]),
                        json.dumps(item["meta"], ensure_ascii=False, default=str), now,
                    )
                    for item in clean
                ],
            )
            self._update_reliability_batch(c, domain, clean, now)

        posterior = self._posterior_from_row(domain_state)
        return {
            "domain": domain,
            "samples": int(posterior["samples"]),
            "reward_ewma": round(float(posterior["reward_ewma"]), 4),
            "residual_ewma": round(float(posterior["residual_ewma"]), 4),
            "posterior_mean": {
                name: round(value, 4)
                for name, value in zip(self.FEATURE_NAMES, posterior["mean"])
            },
            "updated_calls": len(clean),
            "write_transactions": 1,
            "write_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    def snapshot(self, domain: str) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            key = self._key(domain, "domain")
            raw = c.execute("SELECT * FROM routing_policy WHERE policy_key=?", (key,)).fetchone()
        posterior = self._posterior_from_row(
            self._decode_row(raw, self._default_row(domain, "domain"))
        )
        return {
            "domain": domain,
            "samples": int(posterior["samples"]),
            "reward_ewma": round(float(posterior["reward_ewma"]), 4),
            "residual_ewma": round(float(posterior["residual_ewma"]), 4),
            "posterior_mean": {
                name: round(value, 4)
                for name, value in zip(self.FEATURE_NAMES, posterior["mean"])
            },
        }


class AdaptiveDecisionPolicy(DecisionPolicy):
    """EvoGain-APR: posterior routing with learned abstention advantage."""

    def __init__(self, planner, registry, sandbox, skills, *, max_calls: int, max_delegations: int):
        super().__init__(planner, registry, sandbox, skills, max_calls=max_calls, max_delegations=max_delegations)
        self.routing = AdaptiveRoutingStore(getattr(skills, "path", "outputs/runtime.db"))

    def _base_features(
        self,
        *,
        tool: str,
        cost: float,
        goal: Any,
        missing: list[str],
        previous: list[Any],
        skills: list[Any],
        reliability: float,
    ) -> tuple[dict[str, float], set[str]]:
        meta = self._tool_meta(tool)
        channels = set(self.TOOL_CHANNELS.get(tool, set()))
        channels.update(str(value) for value in meta["evidence_tags"])
        channel_terms = self._terms(channels | {meta["purpose"]})
        targets = list(missing) or list(goal.required_evidence)
        coverage_scores = []
        for target in targets:
            target_terms = self._terms(target)
            overlap = target_terms & channel_terms
            if overlap:
                coverage_scores.append(min(1.0, 0.42 + 0.18 * len(overlap)))
            elif tool == "evidence.search":
                coverage_scores.append(0.34)
            else:
                coverage_scores.append(0.0)
        coverage = sum(coverage_scores) / max(1, len(coverage_scores))
        authority = 1.0 if meta["mode"] == "mcp-read" and meta["evidence_tags"] else 0.0
        skill_support = max((skill.posterior_mean for skill in skills if tool in skill.preferred_tools), default=0.0)
        prior_success = sum(1 for result in previous if result.ok and result.tool == tool)
        novelty = 1.0 / (1.0 + 0.72 * prior_success)
        contradiction = 1.0 if tool in self.CONTRADICTION_TOOLS and bool(targets) else 0.0
        specificity = min(1.0, len(channel_terms) / 10.0)
        cost_pressure = min(1.0, max(0.0, float(cost)) / 3.0)
        gap_pressure = min(1.0, len(targets) / max(1, len(goal.required_evidence) or len(targets)))
        return {
            "bias": 1.0,
            "coverage": coverage,
            "authority": authority,
            "skill_support": skill_support,
            "novelty": novelty,
            "contradiction": contradiction,
            "specificity": specificity,
            "tool_reliability": max(0.0, min(1.0, float(reliability))),
            "cost_pressure": cost_pressure,
            "redundancy": 0.0,
            "gap_pressure": gap_pressure,
            "recovery_context": 1.0 if previous else 0.0,
        }, {value.lower() for value in channel_terms}

    def _vector(self, features: dict[str, float], *, redundancy: float) -> list[float]:
        values = dict(features)
        values["redundancy"] = max(0.0, min(1.0, float(redundancy)))
        return [float(values.get(name, 0.0)) for name in self.routing.FEATURE_NAMES]

    def _rank_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        goal: Any,
        missing: list[str],
        previous: list[Any],
        skills: list[Any],
        budget: float,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        started = time.perf_counter()
        exploration = max(
            0.0,
            min(1.0, float(self.skills.policy(goal.domain.value).get("exploration", 0.6))),
        )
        tools = [str(candidate.get("tool") or "") for candidate in candidates]
        prepared = self.routing.prepare_context(
            goal.domain.value,
            tools=tools,
            exploration=exploration,
        )
        pool = []
        for candidate in candidates:
            tool = str(candidate["tool"])
            features, channels = self._base_features(
                tool=tool,
                cost=candidate["cost"],
                goal=goal,
                missing=missing,
                previous=previous,
                skills=skills,
                reliability=float(prepared["reliability"].get(tool, 0.5)),
            )
            pool.append({**candidate, "features": features, "channels": channels})

        baseline = float(self.routing.score_prepared(self.routing.abstain_vector(), prepared)["score"])
        selected: list[dict[str, Any]] = []
        selected_channels: set[str] = set()
        remaining_budget = max(0.0, float(budget))
        trace: list[dict[str, Any]] = []

        while pool and len(selected) < max(1, int(limit)):
            scored = []
            for item in pool:
                if item["cost"] > remaining_budget + 1e-9:
                    scored.append((-1e9, -1e9, item, 0.0, None, None))
                    continue
                overlap = (
                    len(item["channels"] & selected_channels) / max(1, len(item["channels"]))
                    if selected_channels else 0.0
                )
                vector = self._vector(item["features"], redundancy=overlap)
                policy = self.routing.score_prepared(vector, prepared)
                score = float(policy["score"])
                advantage = score - baseline
                scored.append((advantage, score, item, overlap, vector, policy))
            scored.sort(
                key=lambda row: (row[0], row[1], -float(row[2]["cost"]), str(row[2]["tool"])),
                reverse=True,
            )
            advantage, score, best, overlap, vector, policy = scored[0]
            pool.remove(best)
            if vector is None or policy is None:
                trace.append({"tool": best["tool"], "selected": False, "reason": "budget"})
                continue

            choose = advantage > 0.0
            item_trace = {
                "tool": best["tool"],
                "selected": choose,
                "utility": round(score, 4),
                "abstain_baseline": round(baseline, 4),
                "advantage": round(advantage, 4),
                "diversity_overlap": round(overlap, 4),
                "coverage": round(best["features"]["coverage"], 4),
                "authority": round(best["features"]["authority"], 4),
                "novelty": round(best["features"]["novelty"], 4),
                "skill": round(best["features"]["skill_support"], 4),
                "reliability": round(best["features"]["tool_reliability"], 4),
                "cost": round(float(best["cost"]), 4),
                "policy_mode": policy["mode"],
                "policy_samples": int(policy["samples"]),
                "policy_activation": round(float(policy["activation"]), 4),
                "policy_prior": round(float(policy["prior"]), 4),
                "policy_posterior": round(float(policy["posterior"]), 4),
                "policy_uncertainty": round(float(policy["uncertainty"]), 4),
                "feature_vector": [round(float(value), 5) for value in vector],
            }
            if not choose:
                item_trace["reason"] = "abstain_nonpositive_advantage"
                trace.append(item_trace)
                continue
            selected.append(best)
            selected_channels.update(best["channels"])
            remaining_budget -= float(best["cost"])
            trace.append(item_trace)

        rank_ms = (time.perf_counter() - started) * 1000.0
        for row in trace:
            row["routing_ms"] = round(rank_ms, 3)
            row["posterior_prepare_ms"] = round(float(prepared["prepare_ms"]), 3)
        for item in pool:
            if len(trace) >= 16:
                break
            trace.append({
                "tool": item["tool"],
                "selected": False,
                "reason": "budget_or_lower_advantage",
                "coverage": round(item["features"]["coverage"], 4),
                "cost": round(float(item["cost"]), 4),
                "routing_ms": round(rank_ms, 3),
            })
        return selected, trace[:16]
