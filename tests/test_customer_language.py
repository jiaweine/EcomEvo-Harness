from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUSTOMER = (ROOT / "frontend/customer-language.js").read_text(encoding="utf-8")
DESIGN = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
COPY = (ROOT / "docs/CUSTOMER_COPY.md").read_text(encoding="utf-8")


def test_customer_runtime_pulse_uses_business_language():
    assert "function customerizeRuntimePulse()" in CUSTOMER
    for label in ("处理概况", "资料情况", "当前状态", "已处理", "下一步", "服务连接", "处理方式"):
        assert label in CUSTOMER
    for customer_value in ("资料已齐全", "正在核对", "自动处理", "本地处理", "信息已同步"):
        assert customer_value in CUSTOMER


def test_customer_copy_source_of_truth_bans_engineering_vocabulary():
    assert "普通客户界面禁止直接出现" in COPY
    assert "Never expose these words in normal customer UI" in DESIGN
    for term in ("Runtime", "Agent", "Plugin", "Verifier", "Planner", "Trace", "Provenance", "Authority"):
        assert term in DESIGN
        assert term in COPY


def test_customer_theme_is_loaded_as_final_presentation_layer():
    loader = (ROOT / "frontend/enhancements.js").read_text(encoding="utf-8")
    assert "/assets/customer-language.js" in loader
    assert loader.index("/assets/customer-language.js") > loader.index("/assets/realtime-reconcile.js")
    assert "customer-service" in loader
