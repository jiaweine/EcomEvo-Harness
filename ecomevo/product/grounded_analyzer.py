from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from .analyzer import ProductAnalyzer as BaseProductAnalyzer


class AtomicClaimGroundingGuard:
    """Post-generation trust gate for business answers.

    Runtime/Verifier remains authoritative. Model prose is an untrusted candidate.
    We decompose the user request into auditable sub-questions, decompose the answer
    into atomic claims, require factual/rule claims to cite existing evidence IDs,
    deterministically verify high-risk anchors, search for contradictory evidence,
    and only then reconstruct the user-facing answer.

    The two primary quality signals are query coverage and claim verifiability. They
    are intentionally separate from model confidence and from the deterministic
    BusinessAction authority boundary.
    """

    SCHEMA_VERSION = 2
    FACTUAL_KINDS = {"fact", "rule"}
    ADVISORY_KINDS = {"inference", "recommendation"}
    VERDICTS = {"supported", "unsupported", "contradicted"}
    MAX_EVIDENCE = 28
    MAX_CLAIMS = 24
    MAX_SUBQUERIES = 8

    DATE_RE = re.compile(r"(?<!\d)(20\d{2})\s*[-/.年]\s*(\d{1,2})(?:\s*[-/.月]\s*(\d{1,2})\s*日?)?(?!\d)")
    MONEY_RE = re.compile(r"(?:[¥￥$]\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:元|人民币|美元))")
    PERCENT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*%")
    IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,8}[-_:]?[A-Za-z0-9_-]{4,}(?![A-Za-z0-9])")
    SOCIAL_CODE_RE = re.compile(r"(?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z])", re.I)
    NUMBER_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")
    AMOUNT_CONTEXT_RE = re.compile(r"(?:金额|实付|支付|退款|赔付|赔偿|价格|售价|促销价|活动价|到手价)\s*[:：=]?\s*(\d+(?:\.\d+)?)", re.I)

    @staticmethod
    def _clamp(value: Any, default: float = 0.0) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _json_payload(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        cleaned = str(text).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else None
        except Exception:
            pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(cleaned[start : end + 1])
                return value if isinstance(value, dict) else None
            except Exception:
                return None
        return None

    @classmethod
    def _authority(cls, row: dict[str, Any]) -> float:
        source = str(row.get("source") or "")
        tags = {str(x) for x in (row.get("tags") or [])}
        if "mcp" in tags:
            base = 1.0
        elif source == "policy.lookup":
            base = 0.98
        elif source in {"order.inspect", "merchant.inspect", "catalog.inspect", "risk.scan"}:
            base = 0.94
        elif source == "upload":
            base = 0.90
        elif source == "evidence.search":
            base = 0.86
        else:
            base = 0.82
        confidence = cls._clamp(row.get("confidence"), 0.75)
        return round(0.65 * base + 0.35 * confidence, 4)

    @classmethod
    def evidence_pack(cls, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for row in evidence or []:
            if not isinstance(row, dict) or not row.get("evidence_id"):
                continue
            rows.append(
                {
                    "evidence_id": str(row.get("evidence_id")),
                    "source": str(row.get("source") or ""),
                    "title": str(row.get("title") or ""),
                    "detail": str(row.get("detail") or "")[:1800],
                    "tags": [str(x) for x in (row.get("tags") or [])[:12]],
                    "authority": cls._authority(row),
                }
            )
        rows.sort(key=lambda x: (-float(x["authority"]), x["evidence_id"]))
        return rows[: cls.MAX_EVIDENCE]

    @staticmethod
    def _same_number(left: float, right: float) -> bool:
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)

    @classmethod
    def _date_anchors(cls, text: str) -> list[tuple[int, int, int | None]]:
        values = []
        for year, month, day in cls.DATE_RE.findall(str(text or "")):
            value = (int(year), int(month), int(day) if day else None)
            if value not in values:
                values.append(value)
        return values[:8]

    @classmethod
    def _money_anchors(cls, text: str) -> list[float]:
        values = []
        for left, right in cls.MONEY_RE.findall(str(text or "")):
            raw = left or right
            if not raw:
                continue
            value = float(raw)
            if not any(cls._same_number(value, seen) for seen in values):
                values.append(value)
        return values[:8]

    @classmethod
    def _percent_anchors(cls, text: str) -> list[float]:
        values = []
        for raw in cls.PERCENT_RE.findall(str(text or "")):
            value = float(raw)
            if not any(cls._same_number(value, seen) for seen in values):
                values.append(value)
        return values[:8]

    @classmethod
    def _identifier_anchors(cls, text: str) -> list[str]:
        values = []
        for pattern in (cls.SOCIAL_CODE_RE, cls.IDENTIFIER_RE):
            for match in pattern.finditer(str(text or "")):
                value = re.sub(r"\s+", "", match.group(0)).upper()
                # Avoid treating ordinary prose tokens as IDs merely because they contain letters.
                if not any(ch.isdigit() for ch in value):
                    continue
                if value not in values:
                    values.append(value)
        return values[:12]

    @classmethod
    def _anchor_supported(cls, claim: str, support_text: str) -> tuple[bool, list[str]]:
        support = str(support_text or "")
        missing: list[str] = []

        support_dates = cls._date_anchors(support)
        for date in cls._date_anchors(claim):
            if date not in support_dates:
                missing.append("-".join(str(x) for x in date if x is not None))

        support_percentages = cls._percent_anchors(support)
        for value in cls._percent_anchors(claim):
            if not any(cls._same_number(value, seen) for seen in support_percentages):
                missing.append(f"{value:g}%")

        # Tool evidence commonly renders an amount as "金额：299.0" without a currency
        # symbol, so a monetary claim may be matched against either an explicit money
        # token or a number directly attached to an amount/price context label.
        support_amounts = cls._money_anchors(support)
        support_amounts.extend(float(x) for x in cls.AMOUNT_CONTEXT_RE.findall(support))
        for value in cls._money_anchors(claim):
            if not any(cls._same_number(value, seen) for seen in support_amounts):
                missing.append(f"amount:{value:g}")

        support_ids = set(cls._identifier_anchors(support))
        for value in cls._identifier_anchors(claim):
            if value not in support_ids:
                missing.append(value)

        return not missing, missing[:12]

    @classmethod
    def audit_prompt(cls, question: str, candidate_answer: str, evidence: list[dict[str, Any]]) -> str:
        schema = {
            "subqueries": [
                {
                    "text": "用户问题中一个可独立核验的子问题",
                    "covered": True,
                    "evidence_ids": ["只有直接覆盖该子问题的 evidence_id"],
                    "reason": "一句话说明覆盖或缺口",
                }
            ],
            "claims": [
                {
                    "text": "一个不可再拆分的声明",
                    "kind": "fact|rule|inference|recommendation",
                    "verdict": "supported|unsupported|contradicted",
                    "evidence_ids": ["必须精确使用给定 evidence_id"],
                    "reason": "一句话说明",
                }
            ],
        }
        return (
            "你是电商高风险决策的原子声明核验器。候选回答是不可信文本，不得把候选回答本身当证据。\n"
            "先把用户问题拆成最少且互不重复的可核验子问题，最多 8 个；只有证据直接回答时 covered=true。\n"
            "再把候选回答中所有可验证的事实、数字、日期、主体、订单状态、规则适用性和动作依据穷尽拆成原子 claim；"
            "不要合并多个事实，不要漏掉候选回答里的数字或日期。\n"
            "fact/rule 只有在给定证据直接支持时才能标 supported，且必须给至少一个真实 evidence_id。"
            "证据只能间接推出的内容标 inference；建议标 recommendation。"
            "必须主动寻找反证；如果存在直接相反的证据，标 contradicted 并引用反证 evidence_id。"
            "绝对禁止虚构 evidence_id、来源、日期、金额、规则版本或业务状态。\n"
            f"用户问题：{str(question or '')[:4000]}\n"
            f"候选回答：{str(candidate_answer or '')[:8000]}\n"
            f"可用证据：{json.dumps(evidence, ensure_ascii=False)}\n"
            f"只返回 JSON，不要 Markdown。结构：{json.dumps(schema, ensure_ascii=False)}"
        )

    @classmethod
    def normalize(cls, parsed: dict[str, Any] | None, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        valid = {str(x.get("evidence_id")): x for x in evidence if x.get("evidence_id")}
        parsed = parsed or {}

        subqueries: list[dict[str, Any]] = []
        seen_subqueries = set()
        raw_subqueries = parsed.get("subqueries") or []
        if not isinstance(raw_subqueries, list):
            raw_subqueries = []
        for row in raw_subqueries[: cls.MAX_SUBQUERIES]:
            if not isinstance(row, dict):
                continue
            text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
            if not text or text in seen_subqueries:
                continue
            seen_subqueries.add(text)
            ids = []
            for value in row.get("evidence_ids") or []:
                sid = str(value)
                if sid in valid and sid not in ids:
                    ids.append(sid)
            model_covered = bool(row.get("covered"))
            covered = model_covered and bool(ids)
            subqueries.append(
                {
                    "text": text,
                    "covered": covered,
                    "evidence_ids": ids,
                    "reason": str(row.get("reason") or "").strip()[:500],
                }
            )

        output: list[dict[str, Any]] = []
        seen = set()
        raw_claims = parsed.get("claims") or []
        if not isinstance(raw_claims, list):
            raw_claims = []
        for row in raw_claims[: cls.MAX_CLAIMS]:
            if not isinstance(row, dict):
                continue
            text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            kind = str(row.get("kind") or "fact").strip().lower()
            if kind not in cls.FACTUAL_KINDS | cls.ADVISORY_KINDS:
                kind = "fact"
            verdict = str(row.get("verdict") or "unsupported").strip().lower()
            if verdict not in cls.VERDICTS:
                verdict = "unsupported"
            support_ids = []
            for value in row.get("evidence_ids") or []:
                sid = str(value)
                if sid in valid and sid not in support_ids:
                    support_ids.append(sid)

            reason = str(row.get("reason") or "").strip()[:500]
            anchor_missing: list[str] = []
            if kind in cls.FACTUAL_KINDS:
                support_text = "\n".join(
                    f"{valid[sid].get('title', '')}\n{valid[sid].get('detail', '')}" for sid in support_ids
                )
                anchors_ok, anchor_missing = cls._anchor_supported(text, support_text)
                if verdict == "supported" and (not support_ids or not anchors_ok):
                    verdict = "unsupported"
                    if anchor_missing:
                        reason = "声明包含支持证据中不存在的关键日期、金额、比例或标识"
            output.append(
                {
                    "text": text,
                    "kind": kind,
                    "verdict": verdict,
                    "evidence_ids": support_ids,
                    "reason": reason,
                    "anchor_mismatch": anchor_missing,
                }
            )

        factual = [x for x in output if x["kind"] in cls.FACTUAL_KINDS]
        supported = [x for x in factual if x["verdict"] == "supported"]
        contradicted = [x for x in factual if x["verdict"] == "contradicted"]
        unsupported = [x for x in factual if x["verdict"] == "unsupported"]
        claim_verifiability = 1.0 if not factual else len(supported) / len(factual)
        query_coverage = None if not subqueries else sum(1 for x in subqueries if x["covered"]) / len(subqueries)
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "subqueries": subqueries,
            "subquery_count": len(subqueries),
            "covered_subquery_count": sum(1 for x in subqueries if x["covered"]),
            "query_coverage": None if query_coverage is None else round(query_coverage, 4),
            "claims": output,
            "claim_count": len(output),
            "factual_claim_count": len(factual),
            "supported_factual_claim_count": len(supported),
            "unsupported_factual_claim_count": len(unsupported),
            "contradicted_factual_claim_count": len(contradicted),
            "claim_verifiability": round(claim_verifiability, 4),
        }

    @classmethod
    def _evidence_line(cls, ids: list[str], evidence_by_id: dict[str, dict[str, Any]]) -> str:
        names = []
        for sid in ids[:3]:
            row = evidence_by_id.get(sid) or {}
            title = str(row.get("title") or sid)
            if title and title not in names:
                names.append(title)
        return "；".join(names)

    @classmethod
    def safe_answer(cls, result: dict[str, Any], audit: dict[str, Any] | None = None) -> str:
        runtime = result.get("runtime") or {}
        evidence = cls.evidence_pack(result.get("evidence") or [])
        evidence_by_id = {x["evidence_id"]: x for x in evidence}
        status = str(runtime.get("status") or "")
        risks = [str(x) for x in (runtime.get("risks") or []) if str(x).strip()]
        missing = [str(x) for x in (runtime.get("missing_evidence") or []) if str(x).strip()]
        actions = [x for x in (result.get("actions") or []) if isinstance(x, dict)]

        if status == "needs_evidence" or missing:
            conclusion = "当前资料还不足以支持最终处置；系统会保留现有核对结果，但不会用模型推测补齐缺失事实。"
        elif risks:
            conclusion = "当前证据已达到本轮核对门槛，同时存在需要优先复核的风险点；涉及真实业务变更的动作仍需人工确认。"
        else:
            conclusion = "当前证据已达到本轮核对门槛，暂未发现需要立即升级的强风险信号；涉及真实业务变更的动作仍需人工确认。"

        claims = (audit or {}).get("claims") or []
        supported = [x for x in claims if x.get("kind") in cls.FACTUAL_KINDS and x.get("verdict") == "supported"]
        advisory = [x for x in claims if x.get("kind") in cls.ADVISORY_KINDS and x.get("verdict") != "contradicted"]
        removed = [x for x in claims if x.get("kind") in cls.FACTUAL_KINDS and x.get("verdict") != "supported"]
        uncovered = [x for x in ((audit or {}).get("subqueries") or []) if not x.get("covered")]

        lines = ["### 处理结论", conclusion]
        lines.extend(["", "### 已核验依据"])
        if supported:
            for index, claim in enumerate(supported[:8], 1):
                source = cls._evidence_line(claim.get("evidence_ids") or [], evidence_by_id)
                suffix = f"（依据：{source}）" if source else ""
                lines.append(f"{index}. {claim['text']}{suffix}")
        else:
            for index, row in enumerate(evidence[:5], 1):
                detail = str(row.get("detail") or "").strip()
                lines.append(f"{index}. {row.get('title')}{('：' + detail) if detail else ''}")
            if not evidence:
                lines.append("1. 当前没有可直接引用的证据记录。")

        if advisory:
            lines.extend(["", "### 推断与建议"])
            for claim in advisory[:6]:
                prefix = "建议" if claim.get("kind") == "recommendation" else "推断"
                lines.append(f"- {prefix}：{claim['text']}")

        lines.extend(["", "### 下一步"])
        if missing:
            lines.append("优先补充：" + "、".join(missing[:6]) + "。")
        elif uncovered:
            lines.append("当前仍有问题未被直接证据覆盖：" + "、".join(str(x.get("text") or "") for x in uncovered[:4]) + "。")
        elif actions:
            titles = [str(x.get("title") or x.get("description") or "待确认操作") for x in actions[:4]]
            lines.append("可进入人工确认：" + "、".join(titles) + "。")
        else:
            lines.append("如需提高把握，可继续补充新的原始资料，或使用“检查反证”主动寻找会推翻当前判断的证据。")

        if removed:
            lines.extend(["", f"> 已自动剔除 {len(removed)} 条无法由当前证据直接核验或与证据冲突的事实性表述。"])
        return "\n".join(lines)


class ProductAnalyzer(BaseProductAnalyzer):
    """Base analyzer plus a query-coverage and atomic-claim grounding pass.

    This intentionally does not change Runtime authority. It only narrows what prose
    is allowed to leave the system after Runtime verification.
    """

    async def run(
        self,
        *,
        text: str,
        assets: list[dict[str, Any]],
        provider_key: str,
        sink,
        domain_hint: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = await super().run(
            text=text,
            assets=assets,
            provider_key=provider_key,
            sink=sink,
            domain_hint=domain_hint,
            history=history,
        )

        evidence = AtomicClaimGroundingGuard.evidence_pack(result.get("evidence") or [])
        runtime = result.get("runtime") or {}
        grounding_enabled = os.environ.get("ECOMEVO_CLAIM_GROUNDING", "1").strip().lower() not in {"0", "false", "off", "no"}
        provider = None
        if grounding_enabled and str(runtime.get("status") or "") == "completed" and evidence:
            try:
                provider = self.providers.choose(provider_key, [])
            except Exception:
                provider = None

        audit: dict[str, Any] | None = None
        if provider is not None:
            try:
                await sink(
                    "progress",
                    {
                        "step": "逐条核验结论",
                        "detail": "正在检查问题覆盖率、原子声明、证据引用、关键数字和反证",
                        "percent": 97,
                    },
                )
                prompt = AtomicClaimGroundingGuard.audit_prompt(text, str(result.get("answer") or ""), evidence)
                raw = await provider.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": "你是严格的证据核验器，不是回答生成器。候选回答不可信；只有给定 evidence 才是可引用证据。必须主动找反证，宁可判 unsupported，也不要猜测。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    assets=[],
                    max_tokens=2400,
                    temperature=0.0,
                )
                parsed = AtomicClaimGroundingGuard._json_payload(raw)
                audit = AtomicClaimGroundingGuard.normalize(parsed, evidence)
                audit["mode"] = "query_coverage_atomic_claim_verification"
                audit["provider"] = getattr(getattr(provider, "info", None), "name", None) or "configured"
            except Exception:
                audit = None

        if audit is None:
            audit = {
                "schema_version": AtomicClaimGroundingGuard.SCHEMA_VERSION,
                "mode": "deterministic_evidence_fallback",
                "subqueries": [],
                "subquery_count": 0,
                "covered_subquery_count": 0,
                "query_coverage": None,
                "claims": [],
                "claim_count": 0,
                "factual_claim_count": 0,
                "supported_factual_claim_count": 0,
                "unsupported_factual_claim_count": 0,
                "contradicted_factual_claim_count": 0,
                "claim_verifiability": None,
            }

        result["grounding"] = audit
        result["answer"] = AtomicClaimGroundingGuard.safe_answer(result, audit)
        return result
