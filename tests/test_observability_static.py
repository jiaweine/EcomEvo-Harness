from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_observability_ui_never_presents_model_score_as_truth_confidence():
    html = (ROOT / "frontend" / "observability.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "observability.js").read_text(encoding="utf-8")
    combined = html + js
    assert "不是模型置信度" in combined
    assert "verifier_score" not in combined
    assert "confidence" not in combined.lower()
    assert "未采集" in combined
    assert "Operator Hours" in html


def test_observability_frontend_is_read_only():
    js = (ROOT / "frontend" / "observability.js").read_text(encoding="utf-8")
    assert "/api/runtime/observability" in js
    assert "/api/actions" not in js
    assert "tools/call" not in js
    assert "method:'POST'" not in js.replace(" ", "")
    assert "method:'PATCH'" not in js.replace(" ", "")
    assert "method:'DELETE'" not in js.replace(" ", "")


def test_observability_page_has_mobile_layout():
    css = (ROOT / "frontend" / "observability.css").read_text(encoding="utf-8")
    assert "@media(max-width:640px)" in css
    assert "@media(max-width:420px)" in css


def test_observability_provider_telemetry_never_estimates_tokens_or_partial_cost_as_total():
    html = (ROOT / "frontend" / "observability.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "observability.js").read_text(encoding="utf-8")
    service = (ROOT / "ecomevo" / "product" / "observability.py").read_text(encoding="utf-8")
    telemetry = (ROOT / "ecomevo" / "providers" / "telemetry.py").read_text(encoding="utf-8")
    combined = html + js + service + telemetry

    assert "Provider-reported" in combined
    assert "不估算 Token" in combined
    assert "非总成本" in js
    assert "exact provider:model" in html
    assert "text length is never converted into estimated tokens" in service
    assert "known_cost_by_currency" in service
    assert "ECOMEVO_PROVIDER_PRICING_JSON" in telemetry
    assert "len(text)" not in telemetry
    assert "approx" not in telemetry.lower()
