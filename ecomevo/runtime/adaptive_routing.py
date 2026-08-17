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
    """Persistent hierarchical Bayesian linear policy for read-only tool routing.

    The bootstrap vector is a prior, not a permanent value function. Observed routing
    outcomes update both a global posterior and a domain posterior. Scoring remains
    deterministic (UCB rather than random Thompson samples) and only affects cognition;
    the sandbox, verifier and authority gates remain outside this learner.
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
    # Cold-start prior. Its influence vanishes as A,b accumulate real outcomes.
    PRIOR_MEAN = (0.02, 1.45, 0.55, 0.40, 0.25, 0.12, 0.08, 0.28, -0.45, -0.30, 0.08, 0.0)
    PRIOR_PRECISION = 3.0
    DECAY = 0.997
    DOMAIN_SHRINKAGE = 24.0
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

    def _prior_a(self) -> list[list[float]]:
        return [[self.PRIOR_PRECISION if i == j else 0.0 for j in range(self.dim)] for i in range(self.dim)]

    def _prior_b(self) -> list[float]:
        return [self.PRIOR_PRECISION * x for x in self.PRIOR_MEAN]

    def _init(self):
        with self._conn() as c:
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

    def _ensure(self, c: sqlite3.Connection, domain: str, scope: str):
        key = self._key(domain, scope)
        row = c.execute("SELECT policy_key FROM routing_policy WHERE policy_key=?", (key,)).fetchone()
        if row:
            return
        c.execute(
            "INSERT INTO routing_policy(policy_key,domain,scope,a_json,b_json,samples,reward_ewma,residual_ewma,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (key, domain, scope, json.dumps(self._prior_a()), json.dumps(self._prior_b()), 0, 0.0, 0.25, time.time()),
        )

    def _row(self, domain: str, scope: str) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._ensure(c, domain, scope)
            row = c.execute("SELECT * FROM routing_policy WHERE policy_key=?", (self._key(domain, scope),)).fetchone()
        data = dict(row)
        data["a"] = [[float(x) for x in r] for r in json.loads(data.pop("a_json"))]
        data["b"] = [float(x) for x in json.loads(data.pop("b_json"))]
        return data

    @staticmethod
    def _inverse(matrix: list[list[float]]) -> list[list[float]]:
        n = len(matrix)
        aug = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
        for col in range(n):
            pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
            if abs(aug[pivot][col]) < 1e-10:
                aug[col][col] += 1e-6
                pivot = col
            if pivot != col:
                aug[col], aug[pivot] = aug[pivot], aug[col]
            scale = aug[col][col]
            aug[col] = [v / scale for v in aug[col]]
            for r in range(n):
                if r == col:
                    continue
                factor = aug[r][col]
                if abs(factor) < 1e-14:
                    continue
                aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col])]
        return [row[n:] for row in aug]

    @staticmethod
    def _matvec(matrix: list[list[float]], vector: list[float]) -> list[float]:
        return [sum(a * b for a, b in zip(row, vector)) for row in matrix]

    @staticmethod
    def _dot(a: Iterable[float], b: Iterable[float]) -> float:
        return sum(float(x) * float(y) for x, y in zip(a, b))

    def _posterior(self, domain: str, scope: str) -> dict[str, Any]:
        row = self._row(domain, scope)
        inv = self._inverse(row["a"])
        mean = self._matvec(inv, row["b"])
        return {**row, "inverse": inv, "mean": mean}

    def prepare(self, domain: str, *, exploration: float) -> dict[str, Any]:
        global_p = self._posterior("*", "global")
        domain_p = self._posterior(domain, "domain")
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
            "global": global_p, "domain": domain_p, "tau": tau, "residual": residual,
            "activation": activation, "mode": mode, "beta": beta, "samples": n,
        }

    def score_prepared(self, vector: list[float], prepared: dict[str, Any]) -> dict[str, float | int | str]:
        if len(vector) != self.dim:
            raise ValueError(f"routing feature dimension mismatch: {len(vector)} != {self.dim}")
        global_p, domain_p = prepared["global"], prepared["domain"]
        tau = float(prepared["tau"]); n = int(prepared["samples"])
        global_mean = self._dot(global_p["mean"], vector)
        domain_mean = self._dot(domain_p["mean"], vector)
        posterior_mean = (1.0 - tau) * global_mean + tau * domain_mean
        gv = self._matvec(global_p["inverse"], vector)
        dv = self._matvec(domain_p["inverse"], vector)
        global_var = max(0.0, self._dot(vector, gv))
        domain_var = max(0.0, self._dot(vector, dv))
        uncertainty = math.sqrt(max(0.0, (1.0 - tau) ** 2 * global_var + tau ** 2 * domain_var))
        prior_score = self._dot(self.PRIOR_MEAN, vector)
        optimistic = posterior_mean + float(prepared["beta"]) * uncertainty
        if n < 64:
            optimistic = max(prior_score - 0.70, min(prior_score + 0.70, optimistic))
        activation = float(prepared["activation"])
        final = (1.0 - activation) * prior_score + activation * optimistic
        return {
            "score": float(final), "prior": float(prior_score), "posterior": float(posterior_mean),
            "uncertainty": float(uncertainty), "activation": activation, "samples": n,
            "mode": str(prepared["mode"]), "residual": float(prepared["residual"]),
        }

    def score(self, domain: str, vector: list[float], *, exploration: float) -> dict[str, float | int | str]:
        return self.score_prepared(vector, self.prepare(domain, exploration=exploration))

    def _update_one(self, c: sqlite3.Connection, domain: str, scope: str, vector: list[float], reward: float):
        self._ensure(c, domain, scope)
        key = self._key(domain, scope)
        row = c.execute("SELECT * FROM routing_policy WHERE policy_key=?", (key,)).fetchone()
        a = [[float(x) for x in r] for r in json.loads(row["a_json"])]
        b = [float(x) for x in json.loads(row["b_json"])]
        prior_a, prior_b = self._prior_a(), self._prior_b()

        for i in range(self.dim):
            for j in range(self.dim):
                a[i][j] = prior_a[i][j] + self.DECAY * (a[i][j] - prior_a[i][j])
            b[i] = prior_b[i] + self.DECAY * (b[i] - prior_b[i])

        inv = self._inverse(a)
        pred = self._dot(self._matvec(inv, b), vector)
        residual = abs(float(reward) - pred)
        for i, xi in enumerate(vector):
            b[i] += float(reward) * xi
            for j, xj in enumerate(vector):
                a[i][j] += xi * xj
        reward_ewma = 0.92 * float(row["reward_ewma"]) + 0.08 * float(reward)
        residual_ewma = 0.90 * float(row["residual_ewma"]) + 0.10 * residual
        c.execute(
            "UPDATE routing_policy SET a_json=?,b_json=?,samples=samples+1,reward_ewma=?,residual_ewma=?,updated_at=? WHERE policy_key=?",
            (json.dumps(a), json.dumps(b), reward_ewma, residual_ewma, time.time(), key),
        )

    def update(self, domain: str, vector: list[float], reward: float, *, phase: str, tool: str, meta: dict[str, Any]):
        reward = max(-1.25, min(1.50, float(reward)))
        if len(vector) != self.dim:
            return
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            self._update_one(c, "*", "global", vector, reward)
            self._update_one(c, domain, "domain", vector, reward)
            c.execute(
                "INSERT INTO routing_outcomes(domain,phase,tool,reward,feature_json,meta_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (domain, str(phase)[:40], str(tool)[:120], reward, json.dumps(vector),
                 json.dumps(meta, ensure_ascii=False, default=str), time.time()),
            )

    def tool_reliability(self, domain: str, tool: str) -> float:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT alpha,beta FROM routing_tool_stats WHERE domain=? AND tool=?", (domain, tool)).fetchone()
            grow = c.execute("SELECT alpha,beta FROM routing_tool_stats WHERE domain='*' AND tool=?", (tool,)).fetchone()
        local = (float(row["alpha"]) / max(1e-9, float(row["alpha"]) + float(row["beta"]))) if row else 0.5
        global_mean = (float(grow["alpha"]) / max(1e-9, float(grow["alpha"]) + float(grow["beta"]))) if grow else 0.5
        return 0.35 * global_mean + 0.65 * local

    def update_tool_reliability(self, domain: str, tool: str, *, ok: bool, reward: float):
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            for d in ("*", domain):
                row = c.execute("SELECT * FROM routing_tool_stats WHERE domain=? AND tool=?", (d, tool)).fetchone()
                if row:
                    alpha = float(row["alpha"]) + (1.0 if ok else 0.0)
                    beta = float(row["beta"]) + (0.0 if ok else 1.0)
                    rew = 0.92 * float(row["reward_ewma"]) + 0.08 * float(reward)
                    c.execute("UPDATE routing_tool_stats SET alpha=?,beta=?,uses=uses+1,reward_ewma=?,updated_at=? WHERE domain=? AND tool=?",
                              (alpha, beta, rew, now, d, tool))
                else:
                    c.execute("INSERT INTO routing_tool_stats(domain,tool,alpha,beta,uses,reward_ewma,updated_at) VALUES(?,?,?,?,?,?,?)",
                              (d, tool, 2.0 + (1.0 if ok else 0.0), 2.0 + (0.0 if ok else 1.0), 1, float(reward), now))

    def snapshot(self, domain: str) -> dict[str, Any]:
        p = self._posterior(domain, "domain")
        return {
            "domain": domain,
            "samples": int(p["samples"]),
            "reward_ewma": round(float(p["reward_ewma"]), 4),
            "residual_ewma": round(float(p["residual_ewma"]), 4),
            "posterior_mean": {name: round(value, 4) for name, value in zip(self.FEATURE_NAMES, p["mean"])},
        }


class AdaptiveDecisionPolicy(DecisionPolicy):
    """EvoGain with persistent contextual posterior routing and trajectory credit."""

    def __init__(self, planner, registry, sandbox, skills, *, max_calls: int, max_delegations: int):
        super().__init__(planner, registry, sandbox, skills, max_calls=max_calls, max_delegations=max_delegations)
        self.routing = AdaptiveRoutingStore(getattr(skills, "path", "outputs/runtime.db"))

    def _base_features(self, *, tool: str, cost: float, goal: Any, missing: list[str],
                       previous: list[Any], skills: list[Any]) -> tuple[dict[str, float], set[str]]:
        meta = self._tool_meta(tool)
        channels = set(self.TOOL_CHANNELS.get(tool, set()))
        channels.update(str(x) for x in meta["evidence_tags"])
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
        skill_support = max((s.posterior_mean for s in skills if tool in s.preferred_tools), default=0.0)
        prior_success = sum(1 for result in previous if result.ok and result.tool == tool)
        novelty = 1.0 / (1.0 + 0.72 * prior_success)
        contradiction = 1.0 if tool in self.CONTRADICTION_TOOLS and bool(targets) else 0.0
        specificity = min(1.0, len(channel_terms) / 10.0)
        tool_reliability = self.routing.tool_reliability(goal.domain.value, tool)
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
            "tool_reliability": tool_reliability,
            "cost_pressure": cost_pressure,
            "redundancy": 0.0,
            "gap_pressure": gap_pressure,
            "recovery_context": 1.0 if previous else 0.0,
        }, {x.lower() for x in channel_terms}

    def _vector(self, features: dict[str, float], *, redundancy: float) -> list[float]:
        values = dict(features)
        values["redundancy"] = max(0.0, min(1.0, float(redundancy)))
        return [float(values.get(name, 0.0)) for name in self.routing.FEATURE_NAMES]

    def _rank_candidates(self, candidates: list[dict[str, Any]], *, goal: Any, missing: list[str],
                         previous: list[Any], skills: list[Any], budget: float,
                         limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        pool = []
        for candidate in candidates:
            features, channels = self._base_features(
                tool=candidate["tool"], cost=candidate["cost"], goal=goal,
                missing=missing, previous=previous, skills=skills,
            )
            pool.append({**candidate, "features": features, "channels": channels})

        selected: list[dict[str, Any]] = []
        selected_channels: set[str] = set()
        remaining_budget = max(0.0, float(budget))
        trace: list[dict[str, Any]] = []
        exploration = max(0.0, min(1.0, float(self.skills.policy(goal.domain.value).get("exploration", 0.6))))
        prepared = self.routing.prepare(goal.domain.value, exploration=exploration)

        while pool and len(selected) < max(1, int(limit)):
            scored = []
            for item in pool:
                if item["cost"] > remaining_budget + 1e-9:
                    scored.append((-1e9, item, 0.0, None, None))
                    continue
                overlap = (len(item["channels"] & selected_channels) / max(1, len(item["channels"]))) if selected_channels else 0.0
                vector = self._vector(item["features"], redundancy=overlap)
                policy = self.routing.score_prepared(vector, prepared)
                scored.append((float(policy["score"]), item, overlap, vector, policy))
            scored.sort(key=lambda x: (x[0], -float(x[1]["cost"]), str(x[1]["tool"])), reverse=True)
            score, best, overlap, vector, policy = scored[0]
            pool.remove(best)
            if vector is None or policy is None:
                trace.append({"tool": best["tool"], "selected": False, "reason": "budget"})
                continue
            item_trace = {
                "tool": best["tool"], "selected": bool(score >= self.MIN_EXPECTED_GAIN),
                "utility": round(max(0.0, score), 4), "diversity_overlap": round(overlap, 4),
                "coverage": round(best["features"]["coverage"], 4),
                "authority": round(best["features"]["authority"], 4),
                "novelty": round(best["features"]["novelty"], 4),
                "skill": round(best["features"]["skill_support"], 4),
                "cost": round(float(best["cost"]), 4),
                "policy_mode": policy["mode"], "policy_samples": int(policy["samples"]),
                "policy_activation": round(float(policy["activation"]), 4),
                "policy_prior": round(float(policy["prior"]), 4),
                "policy_posterior": round(float(policy["posterior"]), 4),
                "policy_uncertainty": round(float(policy["uncertainty"]), 4),
                "feature_vector": [round(float(x), 5) for x in vector],
            }
            if score < self.MIN_EXPECTED_GAIN:
                item_trace["reason"] = "low_expected_gain"
                trace.append(item_trace)
                continue
            selected.append(best)
            selected_channels.update(best["channels"])
            remaining_budget -= float(best["cost"])
            trace.append(item_trace)

        for item in pool:
            if len(trace) >= 16:
                break
            trace.append({"tool": item["tool"], "selected": False, "reason": "budget_or_lower_gain",
                          "coverage": round(item["features"]["coverage"], 4), "cost": round(float(item["cost"]), 4)})
        return selected, trace[:16]

    def learn_batch(self, *, domain: str, phase: str, trace: list[dict[str, Any]], results: list[Any],
                    before_score: float, before_missing: list[str], after_verification: Any) -> dict[str, Any] | None:
        selected = [x for x in trace if x.get("selected") and isinstance(x.get("feature_vector"), list)]
        if not selected:
            return None
        by_tool = {str(r.tool): r for r in results}
        before_gap = len(before_missing)
        after_gap = len(list(getattr(after_verification, "missing_evidence", []) or []))
        gap_gain = max(-1.0, min(1.0, (before_gap - after_gap) / max(1, before_gap)))
        score_gain = max(-1.0, min(1.0, float(getattr(after_verification, "score", 0.0)) - float(before_score)))
        passed = 1.0 if bool(getattr(after_verification, "passed", False)) else 0.0
        stagnant = 1.0 if gap_gain <= 0.0 and score_gain <= 0.01 else 0.0
        rewards = []
        for item in selected:
            result = by_tool.get(str(item.get("tool")))
            if result is None:
                continue
            ok = 1.0 if bool(getattr(result, "ok", False)) else 0.0
            cost_pressure = min(1.0, max(0.0, float(getattr(result, "cost", 0.0) or 0.0)) / 3.0)
            coverage = max(0.0, min(1.0, float(item.get("coverage", 0.0))))
            reward = (
                0.46 * gap_gain
                + 0.34 * score_gain
                + 0.16 * passed
                + 0.14 * ok
                + 0.08 * coverage
                - 0.10 * cost_pressure
                - 0.24 * stagnant
                - (0.46 if not ok else 0.0)
            )
            vector = [float(x) for x in item["feature_vector"]]
            tool_name = str(item.get("tool") or "")
            self.routing.update(domain, vector, reward, phase=phase, tool=tool_name, meta={
                "gap_gain": gap_gain, "score_gain": score_gain, "passed": bool(passed),
                "ok": bool(ok), "before_missing": before_gap, "after_missing": after_gap,
            })
            self.routing.update_tool_reliability(domain, tool_name, ok=bool(ok), reward=reward)
            rewards.append(reward)
        if not rewards:
            return None
        snapshot = self.routing.snapshot(domain)
        return {
            "domain": domain,
            "phase": phase,
            "updated_calls": len(rewards),
            "mean_credit": round(sum(rewards) / len(rewards), 4),
            "gap_gain": round(gap_gain, 4),
            "score_gain": round(score_gain, 4),
            "policy": snapshot,
            "authority": "read-only-routing-only",
        }


from .autonomy import AutonomousController
from .delegation import CognitiveDelegator


class AdaptiveAutonomousController(AutonomousController):
    """AutonomousController with safe online credit assignment for EvoGain routing."""

    def __init__(self, planner, registry, executor, sandbox, verifier, reviewer, skills):
        super().__init__(planner, registry, executor, sandbox, verifier, reviewer, skills)
        self.policy = AdaptiveDecisionPolicy(
            planner, registry, sandbox, skills,
            max_calls=self.max_calls, max_delegations=self.max_delegations,
        )
        self.delegator = CognitiveDelegator(reviewer, self.policy)

    async def run(self, *, goal, belief, assets, text, context, reasoner=None, emit):
        initial_missing = list(belief.missing_evidence) or list(goal.required_evidence)
        decisions: dict[int, dict[str, Any]] = {}
        batches: dict[int, list[Any]] = {}
        previous_verification = {"score": 0.0, "missing": initial_missing}

        async def learning_emit(event_type: str, payload: dict[str, Any]):
            nonlocal previous_verification
            await emit(event_type, payload)
            try:
                if event_type == "autonomy.decided":
                    step = int(payload.get("step", 0) or 0)
                    decisions[step] = {
                        "phase": str(payload.get("phase") or ("initial" if step == 0 else "recovery")),
                        "trace": list(payload.get("evogain") or []),
                    }
                    return
                if event_type == "tools.completed":
                    from ecomevo.models import ToolResult
                    batches[0] = [ToolResult(**row) for row in (payload.get("results") or []) if isinstance(row, dict)]
                    return
                if event_type == "tools.recovery_completed":
                    from ecomevo.models import ToolResult
                    step = int(payload.get("step", 0) or 0)
                    batches[step] = [ToolResult(**row) for row in (payload.get("results") or []) if isinstance(row, dict)]
                    return
                if event_type not in {"verification.checked", "verification.rechecked"}:
                    return
                from ecomevo.models import VerificationResult
                step = int(payload.get("step", 0) or 0) if event_type == "verification.rechecked" else 0
                clean = {k: v for k, v in payload.items() if k != "step"}
                verification = VerificationResult(**clean)
                decision = decisions.get(step) or {}
                update = self.policy.learn_batch(
                    domain=goal.domain.value,
                    phase=str(decision.get("phase") or ("initial" if step == 0 else "recovery")),
                    trace=list(decision.get("trace") or []),
                    results=batches.get(step, []),
                    before_score=float(previous_verification["score"]),
                    before_missing=list(previous_verification["missing"]),
                    after_verification=verification,
                )
                previous_verification = {"score": float(verification.score), "missing": list(verification.missing_evidence)}
                if update:
                    await emit("routing.policy.updated", {**update, "step": step})
            except Exception as exc:
                await emit("routing.policy.learning_error", {
                    "error": type(exc).__name__, "authority": "read-only-routing-only",
                })

        return await super().run(
            goal=goal, belief=belief, assets=assets, text=text, context=context,
            reasoner=reasoner, emit=learning_emit,
        )
