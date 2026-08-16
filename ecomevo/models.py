from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class DecisionDomain(str, Enum):
    PRODUCT_GOVERNANCE = "product_governance"
    MERCHANT_REVIEW = "merchant_review"
    AFTERSALES = "aftersales"
    RISK_REVIEW = "risk_review"
    CONTENT_AUDIT = "content_audit"
    GENERAL = "general"


class GoalState(BaseModel):
    primary: str
    domain: DecisionDomain = DecisionDomain.GENERAL
    constraints: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    max_tool_cost: float = 12.0
    risk_tolerance: float = 0.35


class EvidenceRecord(BaseModel):
    evidence_id: str
    source: str
    kind: str
    title: str
    detail: str = ""
    confidence: float = 0.7
    asset_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class BeliefState(BaseModel):
    facts: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.25


class ToolCall(BaseModel):
    call_id: str
    tool: str
    purpose: str
    args: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: float = 1.0
    parallel_group: str = "default"


class ToolResult(BaseModel):
    call_id: str
    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    cost: float = 0.0
    duration_ms: float = 0.0


class SubAgentResult(BaseModel):
    agent: str
    summary: str
    findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    parent_agent: str | None = None
    depth: int = 1
    children: int = 0


class BusinessAction(BaseModel):
    action_id: str
    kind: str
    title: str
    description: str
    side_effect: bool = True
    risk_level: Literal["low", "medium", "high"] = "medium"
    requires_confirmation: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "approved", "executed", "rejected", "failed", "uncertain"] = "proposed"


class VerificationResult(BaseModel):
    passed: bool
    evidence_complete: bool
    constraints_satisfied: bool
    side_effect_safe: bool
    issues: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommendation: Literal["continue", "replan", "rollback", "finish"] = "continue"
    score: float = 0.0


class RuntimeSummary(BaseModel):
    session_id: str
    domain: DecisionDomain
    status: str = "completed"
    tool_calls: int = 0
    subagents: int = 0
    recovery_events: int = 0
    verifier_score: float = 0.0
    evolved: bool = False
    event_chain_valid: bool = True
    proposed_actions: list[BusinessAction] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    belief: BeliefState = Field(default_factory=BeliefState)


class RuntimeEvent(BaseModel):
    session_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    ts: float
    hash: str
    prev_hash: str


class EvolutionPatch(BaseModel):
    patch_id: str
    created_at: float
    reason: str
    target: Literal["prompt", "tool", "memory", "planner"]
    patch: dict[str, Any]
    replay_cases: int
    regression_before: float
    regression_after: float
    accepted: bool
