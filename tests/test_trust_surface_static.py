from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_trust_surface_is_loaded_as_an_independent_enhancement_layer():
    drawer = (ROOT / "frontend" / "drawer-a11y.js").read_text(encoding="utf-8")
    assert "/assets/trust-surface.css" in drawer
    assert "/assets/trust-surface.js" in drawer
    assert "installTrustSurface()" in drawer


def test_trust_surface_uses_evidence_sufficiency_not_model_confidence():
    script = (ROOT / "frontend" / "trust-surface.js").read_text(encoding="utf-8")
    assert "evidence_sufficiency" in script
    assert "证据充分" in script
    assert "证据不足" in script
    assert "存在冲突" in script
    assert "不是模型置信度" in script
    assert "query_coverage" in script
    assert "supported_factual_claim_count" in script
    assert "contradicted_factual_claim_count" in script


def test_trust_surface_exposes_supported_conflicted_and_missing_claim_groups():
    script = (ROOT / "frontend" / "trust-surface.js").read_text(encoding="utf-8")
    assert "已支持" in script
    assert "存在反证" in script
    assert "缺少直接支持" in script
    assert "尚未覆盖的问题" in script


def test_trust_surface_refreshes_from_persisted_result_without_reaching_into_module_state():
    script = (ROOT / "frontend" / "trust-surface.js").read_text(encoding="utf-8")
    assert "new MutationObserver" in script
    assert "/api/conversations/${encodeURIComponent(cid)}" in script
    assert "message.role === 'assistant'" in script
    assert "latest?.payload?.grounding" in script
    assert "latest?.payload?.evidence" in script
    assert "credentials: 'same-origin'" in script
    assert "state.messages" not in script
    assert "const baseRenderEvidence" not in script


def test_trust_surface_mobile_css_keeps_metrics_compact():
    css = (ROOT / "frontend" / "trust-surface.css").read_text(encoding="utf-8")
    assert ".trust-metrics" in css
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in css
    assert "@media (max-width:820px)" in css
