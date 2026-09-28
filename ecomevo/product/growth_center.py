from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc


class GrowthCenter:
    """Durable product control-plane state for GrowthEvo.

    This store intentionally owns *candidate* growth objects, approval state,
    decision evidence and product telemetry. It never dispatches an external
    marketing side effect. Production connectors remain behind the existing
    EcomEvo policy/approval/runtime authority boundary.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS growth_daily_metrics (
                    tenant_id TEXT NOT NULL,
                    metric_date TEXT NOT NULL,
                    incremental_revenue REAL NOT NULL,
                    incremental_profit REAL NOT NULL,
                    avoided_cost REAL NOT NULL,
                    decisions INTEGER NOT NULL,
                    treated INTEGER NOT NULL,
                    no_treatment INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, metric_date)
                );

                CREATE TABLE IF NOT EXISTS growth_campaigns (
                    tenant_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    campaign_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    audience_size INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    evidence_tier TEXT NOT NULL,
                    expected_lift REAL NOT NULL,
                    incremental_roi REAL NOT NULL,
                    budget_rmb REAL NOT NULL,
                    risk_level TEXT NOT NULL,
                    canary_percent REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    starts_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, campaign_id)
                );

                CREATE TABLE IF NOT EXISTS growth_approvals (
                    tenant_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    campaign_id TEXT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    blast_radius INTEGER NOT NULL,
                    budget_rmb REAL NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    note TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (tenant_id, approval_id)
                );

                CREATE TABLE IF NOT EXISTS growth_agent_runs (
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    agent_version TEXT NOT NULL,
                    harness_version TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    policy_status TEXT NOT NULL,
                    evidence_tier TEXT NOT NULL,
                    tool_calls INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    PRIMARY KEY (tenant_id, run_id)
                );

                CREATE TABLE IF NOT EXISTS growth_decisions (
                    tenant_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    action TEXT NOT NULL,
                    expected_incremental_value REAL NOT NULL,
                    no_treatment_value REAL NOT NULL,
                    evidence_tier TEXT NOT NULL,
                    propensity REAL NOT NULL,
                    support_score REAL NOT NULL,
                    uncertainty REAL NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, decision_id)
                );
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

    def _ensure_tenant(self, tenant_id: str) -> None:
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM growth_daily_metrics WHERE tenant_id=? LIMIT 1",
                (tenant_id,),
            ).fetchone()
            if existing:
                return

            today = date.today()
            daily_rows: list[tuple[Any, ...]] = []
            for offset in range(90):
                d = today - timedelta(days=89 - offset)
                seasonal = math.sin(offset / 4.5) * 15000 + math.cos(offset / 8.0) * 9000
                revenue = max(42000.0, 76000 + offset * 720 + seasonal)
                profit = revenue * (0.421 + 0.025 * math.sin(offset / 9.0))
                avoided = max(9000.0, 18000 + offset * 170 + math.sin(offset / 5.0) * 5500)
                decisions = int(30500 + offset * 285 + 2600 * (1 + math.sin(offset / 5.8)))
                no_treatment = int(decisions * (0.21 + 0.035 * math.sin(offset / 10.0)))
                treated = decisions - no_treatment
                daily_rows.append(
                    (
                        tenant_id,
                        d.isoformat(),
                        round(revenue, 2),
                        round(profit, 2),
                        round(avoided, 2),
                        decisions,
                        treated,
                        no_treatment,
                    )
                )
            conn.executemany(
                """
                INSERT INTO growth_daily_metrics
                (tenant_id, metric_date, incremental_revenue, incremental_profit,
                 avoided_cost, decisions, treated, no_treatment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                daily_rows,
            )

            now = self._now()
            campaigns = [
                ("CMP-NEW-USER", "新用户首购提升计划", "Campaign", "running", "首购率", 128421, "App Push", "A", 12.1, 4.8, 286000, "L2", 10.0, -11),
                ("EXP-WINBACK", "沉默用户召回", "Experiment", "canary", "7日活跃", 320510, "Email + Push", "A", 8.7, 3.9, 180000, "L3", 5.0, -13),
                ("CMP-HIGH-VALUE", "高价值用户加购", "Campaign", "running", "加购率", 86210, "In-app", "B", 5.3, 5.1, 94000, "L2", 20.0, -17),
                ("EXP-HOLIDAY", "节日礼品推荐", "Experiment", "analyzing", "GMV", 520331, "Multi-channel", "A", -1.2, 1.2, 420000, "L3", 10.0, -19),
                ("CMP-MEMBER", "会员升级激励", "Campaign", "paused", "会员转化", 72881, "CRM", "B", 9.4, 4.2, 118000, "L2", 10.0, -21),
            ]
            for row in campaigns:
                campaign_id, name, kind, status, objective, audience, channel, tier, lift, roi, budget, risk, canary, day_offset = row
                starts_at = (today + timedelta(days=day_offset)).isoformat()
                conn.execute(
                    """
                    INSERT INTO growth_campaigns
                    (tenant_id, campaign_id, name, campaign_type, status, objective,
                     audience_size, channel, evidence_tier, expected_lift,
                     incremental_roi, budget_rmb, risk_level, canary_percent,
                     created_at, starts_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tenant_id, campaign_id, name, kind, status, objective, audience,
                     channel, tier, lift, roi, budget, risk, canary, now, starts_at, now),
                )

            approvals = [
                ("APR-001", "CMP-NEW-USER", "新用户活动扩大到 25%", "pending", "L3", 186421, 286000, "5% Canary 已通过安全门，申请扩大流量。"),
                ("APR-002", "EXP-WINBACK", "召回实验上线短信渠道", "pending", "L3", 84420, 96000, "新增 SMS 触达，需要渠道与预算审批。"),
            ]
            for approval in approvals:
                conn.execute(
                    """
                    INSERT INTO growth_approvals
                    (tenant_id, approval_id, campaign_id, title, status, risk_level,
                     blast_radius, budget_rmb, summary, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tenant_id, *approval, now),
                )

            agent_runs = [
                (
                    "RUN-GROWTH-001",
                    "growth-agent@1.0",
                    "growth-harness@1.0",
                    "分析新用户首购机会并生成可审批方案",
                    "running",
                    "pass",
                    "A",
                    7,
                    0.18,
                    8420,
                    "已完成受众与证据检查，正在评估因果效果和预算。",
                    [
                        {"label": "分析新用户行为数据", "status": "done"},
                        {"label": "查询历史实验与可用优惠", "status": "done"},
                        {"label": "识别高增量机会人群", "status": "done"},
                        {"label": "生成策略候选方案", "status": "done"},
                        {"label": "评估因果效果与预算", "status": "running"},
                        {"label": "生成创意内容", "status": "queued"},
                        {"label": "创建实验方案", "status": "queued"},
                        {"label": "等待审批", "status": "queued"},
                    ],
                    [
                        {"type": "Audience Draft", "status": "ready"},
                        {"type": "Evidence Report", "status": "ready"},
                        {"type": "Campaign Draft", "status": "building"},
                    ],
                ),
            ]
            for run in agent_runs:
                conn.execute(
                    """
                    INSERT INTO growth_agent_runs
                    (tenant_id, run_id, agent_version, harness_version, task, status,
                     policy_status, evidence_tier, tool_calls, cost_usd, latency_ms,
                     outcome, steps_json, artifacts_json, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tenant_id, *run[:11], json.dumps(run[11], ensure_ascii=False),
                     json.dumps(run[12], ensure_ascii=False), now),
                )

            decision_actions = ["NO_TREATMENT", "FREE_SHIPPING", "APP_PUSH", "EMAIL", "MEMBER_POINTS"]
            subjects = ["新用户 0-7 天", "沉默 30-60 天", "高价值加购", "会员到期", "价格敏感用户"]
            for idx in range(18):
                action = decision_actions[idx % len(decision_actions)]
                expected = 0.0 if action == "NO_TREATMENT" else round(18.5 + idx * 2.7 + math.sin(idx) * 4, 2)
                conn.execute(
                    """
                    INSERT INTO growth_decisions
                    (tenant_id, decision_id, subject, action, expected_incremental_value,
                     no_treatment_value, evidence_tier, propensity, support_score,
                     uncertainty, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        f"DEC-{idx + 1:04d}",
                        subjects[idx % len(subjects)],
                        action,
                        expected,
                        round(4.2 + idx * 0.35, 2),
                        "A" if idx % 3 else "B",
                        round(0.16 + (idx % 6) * 0.11, 3),
                        round(0.72 + (idx % 5) * 0.05, 3),
                        round(0.08 + (idx % 4) * 0.025, 3),
                        "在约束内最大化期望增量价值；无正增量时保留 NO_TREATMENT。",
                        (datetime.now(UTC) - timedelta(hours=idx * 5)).replace(microsecond=0).isoformat(),
                    ),
                )

    @staticmethod
    def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def dashboard(self, tenant_id: str, *, days: int = 30) -> dict[str, Any]:
        self._ensure_tenant(tenant_id)
        days = min(max(int(days), 7), 90)
        with self._connect() as conn:
            metrics = self._rows(
                conn.execute(
                    """
                    SELECT * FROM growth_daily_metrics
                    WHERE tenant_id=? ORDER BY metric_date DESC LIMIT ?
                    """,
                    (tenant_id, days),
                ).fetchall()
            )
            metrics.reverse()
            totals = {
                "incremental_revenue": round(sum(x["incremental_revenue"] for x in metrics), 2),
                "incremental_profit": round(sum(x["incremental_profit"] for x in metrics), 2),
                "avoided_cost": round(sum(x["avoided_cost"] for x in metrics), 2),
                "decisions": sum(x["decisions"] for x in metrics),
                "no_treatment": sum(x["no_treatment"] for x in metrics),
            }
            spend = max(1.0, totals["incremental_revenue"] * 0.226)
            totals["incremental_roi"] = round(totals["incremental_profit"] / spend, 2)
            campaigns = self._rows(
                conn.execute(
                    "SELECT * FROM growth_campaigns WHERE tenant_id=? ORDER BY updated_at DESC LIMIT 8",
                    (tenant_id,),
                ).fetchall()
            )
            approvals = self._rows(
                conn.execute(
                    "SELECT * FROM growth_approvals WHERE tenant_id=? AND status='pending' ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            )
            run = conn.execute(
                "SELECT * FROM growth_agent_runs WHERE tenant_id=? ORDER BY started_at DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()

        return {
            "data_mode": "reference_seed",
            "period_days": days,
            "kpis": totals,
            "time_series": [
                {
                    "date": x["metric_date"],
                    "incremental_revenue": x["incremental_revenue"],
                    "incremental_profit": x["incremental_profit"],
                    "avoided_cost": x["avoided_cost"],
                }
                for x in metrics
            ],
            "channel_mix": [
                {"name": "App Push", "share": 32},
                {"name": "站内消息", "share": 24},
                {"name": "Email", "share": 18},
                {"name": "短信", "share": 12},
                {"name": "广告", "share": 9},
                {"name": "微信", "share": 4},
                {"name": "其他", "share": 1},
            ],
            "campaigns": campaigns,
            "approvals": approvals,
            "active_agent_run": self._decode_run(dict(run)) if run else None,
            "system": {
                "decision_latency_ms": 48,
                "data_freshness_minutes": 5,
                "policy_status": "healthy",
                "agent_automation_ratio": 0.71,
            },
        }

    def opportunities(self, tenant_id: str) -> list[dict[str, Any]]:
        self._ensure_tenant(tenant_id)
        return [
            {
                "id": "OPP-NEW-USER",
                "title": "新用户 0–7 天出现高可信增量机会",
                "audience": 864000,
                "max_incremental_revenue": 1840000,
                "evidence_tier": "A",
                "estimated_uplift_pp": 7.8,
                "support": 0.91,
                "recommended_action": "首单免邮 + App Push",
            },
            {
                "id": "OPP-WINBACK",
                "title": "沉默 30–60 天用户的 ¥10 券边际收益下降",
                "audience": 241000,
                "max_incremental_revenue": 620000,
                "evidence_tier": "A",
                "estimated_uplift_pp": 3.4,
                "support": 0.86,
                "recommended_action": "重新实验 Offer / NO_TREATMENT 边界",
            },
            {
                "id": "OPP-MEMBER",
                "title": "会员到期前 14 天积分激励存在增量空间",
                "audience": 118400,
                "max_incremental_revenue": 390000,
                "evidence_tier": "B",
                "estimated_uplift_pp": 4.9,
                "support": 0.78,
                "recommended_action": "5% Canary 后再扩大",
            },
        ]

    def campaigns(self, tenant_id: str) -> list[dict[str, Any]]:
        self._ensure_tenant(tenant_id)
        with self._connect() as conn:
            return self._rows(
                conn.execute(
                    "SELECT * FROM growth_campaigns WHERE tenant_id=? ORDER BY updated_at DESC",
                    (tenant_id,),
                ).fetchall()
            )

    def create_campaign(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_tenant(tenant_id)
        now = self._now()
        campaign_id = self._id("CMP")
        risk = str(payload.get("risk_level") or "L2")
        row = {
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
            "name": str(payload.get("name") or "未命名增长活动")[:120],
            "campaign_type": str(payload.get("campaign_type") or "Campaign"),
            "status": "draft",
            "objective": str(payload.get("objective") or "增量利润")[:80],
            "audience_size": max(0, int(payload.get("audience_size") or 0)),
            "channel": str(payload.get("channel") or "App Push")[:80],
            "evidence_tier": str(payload.get("evidence_tier") or "C")[:1],
            "expected_lift": float(payload.get("expected_lift") or 0),
            "incremental_roi": float(payload.get("incremental_roi") or 0),
            "budget_rmb": max(0.0, float(payload.get("budget_rmb") or 0)),
            "risk_level": risk,
            "canary_percent": min(100.0, max(0.0, float(payload.get("canary_percent") or 5))),
            "created_at": now,
            "starts_at": payload.get("starts_at"),
            "updated_at": now,
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO growth_campaigns
                (tenant_id, campaign_id, name, campaign_type, status, objective,
                 audience_size, channel, evidence_tier, expected_lift,
                 incremental_roi, budget_rmb, risk_level, canary_percent,
                 created_at, starts_at, updated_at)
                VALUES (:tenant_id, :campaign_id, :name, :campaign_type, :status,
                        :objective, :audience_size, :channel, :evidence_tier,
                        :expected_lift, :incremental_roi, :budget_rmb, :risk_level,
                        :canary_percent, :created_at, :starts_at, :updated_at)
                """,
                row,
            )
        return row

    def request_canary(self, tenant_id: str, campaign_id: str, *, requested_by: str) -> dict[str, Any]:
        self._ensure_tenant(tenant_id)
        with self._lock, self._connect() as conn:
            campaign = conn.execute(
                "SELECT * FROM growth_campaigns WHERE tenant_id=? AND campaign_id=?",
                (tenant_id, campaign_id),
            ).fetchone()
            if not campaign:
                raise KeyError(campaign_id)
            approval_id = self._id("APR")
            now = self._now()
            conn.execute(
                """
                INSERT INTO growth_approvals
                (tenant_id, approval_id, campaign_id, title, status, risk_level,
                 blast_radius, budget_rmb, summary, created_at, note)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    approval_id,
                    campaign_id,
                    f"{campaign['name']} · {campaign['canary_percent']:g}% Canary",
                    max("L3", campaign["risk_level"]),
                    max(1, int(campaign["audience_size"] * campaign["canary_percent"] / 100)),
                    campaign["budget_rmb"],
                    "候选活动通过 Evidence/Policy 检查后，申请有限流量 Canary。",
                    now,
                    f"requested_by={requested_by}",
                ),
            )
            conn.execute(
                "UPDATE growth_campaigns SET status='awaiting_approval', updated_at=? WHERE tenant_id=? AND campaign_id=?",
                (now, tenant_id, campaign_id),
            )
            row = conn.execute(
                "SELECT * FROM growth_approvals WHERE tenant_id=? AND approval_id=?",
                (tenant_id, approval_id),
            ).fetchone()
            return dict(row)

    def approvals(self, tenant_id: str) -> list[dict[str, Any]]:
        self._ensure_tenant(tenant_id)
        with self._connect() as conn:
            return self._rows(
                conn.execute(
                    "SELECT * FROM growth_approvals WHERE tenant_id=? ORDER BY created_at DESC",
                    (tenant_id,),
                ).fetchall()
            )

    def decide_approval(
        self,
        tenant_id: str,
        approval_id: str,
        *,
        decision: str,
        decided_by: str,
        note: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        self._ensure_tenant(tenant_id)
        with self._lock, self._connect() as conn:
            approval = conn.execute(
                "SELECT * FROM growth_approvals WHERE tenant_id=? AND approval_id=?",
                (tenant_id, approval_id),
            ).fetchone()
            if not approval:
                raise KeyError(approval_id)
            if approval["status"] != "pending":
                raise RuntimeError("approval already decided")
            now = self._now()
            status = "approved" if decision == "approve" else "rejected"
            conn.execute(
                """
                UPDATE growth_approvals
                SET status=?, decided_at=?, decided_by=?, note=?
                WHERE tenant_id=? AND approval_id=?
                """,
                (status, now, decided_by, note[:1000], tenant_id, approval_id),
            )
            if approval["campaign_id"]:
                campaign_status = "canary" if decision == "approve" else "draft"
                conn.execute(
                    "UPDATE growth_campaigns SET status=?, updated_at=? WHERE tenant_id=? AND campaign_id=?",
                    (campaign_status, now, tenant_id, approval["campaign_id"]),
                )
            row = conn.execute(
                "SELECT * FROM growth_approvals WHERE tenant_id=? AND approval_id=?",
                (tenant_id, approval_id),
            ).fetchone()
            return dict(row)

    @staticmethod
    def _decode_run(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["steps"] = json.loads(row.pop("steps_json", "[]") or "[]")
        row["artifacts"] = json.loads(row.pop("artifacts_json", "[]") or "[]")
        return row

    def agent_runs(self, tenant_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
        self._ensure_tenant(tenant_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM growth_agent_runs WHERE tenant_id=? ORDER BY started_at DESC LIMIT ?",
                (tenant_id, max(1, min(limit, 100))),
            ).fetchall()
            return [self._decode_run(dict(row)) for row in rows]

    def agent_run(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        self._ensure_tenant(tenant_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM growth_agent_runs WHERE tenant_id=? AND run_id=?",
                (tenant_id, run_id),
            ).fetchone()
            if not row:
                raise KeyError(run_id)
            return self._decode_run(dict(row))

    def plan_goal(self, tenant_id: str, goal: str) -> dict[str, Any]:
        """Compile a natural-language goal into a governed candidate run.

        This is deliberately a proposal plane: it creates structured candidate
        artifacts and an auditable run, but never sends messages or changes a
        production connector.
        """
        self._ensure_tenant(tenant_id)
        clean_goal = " ".join(goal.strip().split())[:1000]
        if not clean_goal:
            raise ValueError("goal is required")
        run_id = self._id("RUN")
        now = self._now()
        steps = [
            {"label": "目标编译与权限范围检查", "status": "done"},
            {"label": "读取可用数据与 Evidence", "status": "done"},
            {"label": "识别增量机会与 NO_TREATMENT 边界", "status": "done"},
            {"label": "生成 Strategy / Audience 候选", "status": "done"},
            {"label": "因果效果、Support 与预算检查", "status": "running"},
            {"label": "生成 Creative / Experiment 草稿", "status": "queued"},
            {"label": "Shadow → Approval → Canary", "status": "queued"},
        ]
        artifacts = [
            {"type": "Strategy Proposal", "status": "ready"},
            {"type": "Audience Draft", "status": "ready"},
            {"type": "Evidence Report", "status": "ready"},
            {"type": "Experiment Draft", "status": "queued"},
            {"type": "Campaign Draft", "status": "queued"},
        ]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO growth_agent_runs
                (tenant_id, run_id, agent_version, harness_version, task, status,
                 policy_status, evidence_tier, tool_calls, cost_usd, latency_ms,
                 outcome, steps_json, artifacts_json, started_at)
                VALUES (?, ?, 'growth-agent@1.0', 'growth-harness@1.0', ?, 'running',
                        'pass', 'B', 5, 0.11, 3120, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    run_id,
                    clean_goal,
                    "候选方案已进入 Causal/Policy 检查；任何外部执行仍需 Approval/Canary。",
                    json.dumps(steps, ensure_ascii=False),
                    json.dumps(artifacts, ensure_ascii=False),
                    now,
                ),
            )
        return self.agent_run(tenant_id, run_id)

    def decisions(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_tenant(tenant_id)
        with self._connect() as conn:
            return self._rows(
                conn.execute(
                    "SELECT * FROM growth_decisions WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, max(1, min(limit, 200))),
                ).fetchall()
            )
