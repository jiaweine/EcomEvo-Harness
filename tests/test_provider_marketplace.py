from pathlib import Path

from ecomevo.providers.registry import ProviderRegistry


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/provider-marketplace.css").read_text(encoding="utf-8")
JS = (ROOT / "frontend/provider-marketplace.js").read_text(encoding="utf-8")
POLISH = (ROOT / "frontend/customer-polish.js").read_text(encoding="utf-8")


def test_provider_registry_exposes_major_ai_brands():
    rows = {row["key"]: row for row in ProviderRegistry().list()}
    expected = {
        "openai": ("OpenAI", "OpenAI"),
        "anthropic": ("Claude", "Anthropic"),
        "gemini": ("Gemini", "Google"),
        "deepseek": ("DeepSeek", "DeepSeek"),
        "qwen": ("通义千问", "阿里云百炼"),
        "doubao": ("豆包", "火山引擎方舟"),
        "kimi": ("Kimi", "Moonshot AI"),
        "zhipu": ("智谱 GLM", "智谱 AI"),
        "hunyuan": ("腾讯混元", "腾讯云 TokenHub"),
        "qianfan": ("百度千帆", "百度智能云"),
    }
    assert expected.keys() <= rows.keys()
    for key, (name, vendor) in expected.items():
        assert rows[key]["name"] == name
        assert rows[key]["vendor"] == vendor
        assert not rows[key]["name"].startswith("认知引擎")
    assert rows["auto"]["name"] == "自动选择"
    assert rows["demo"]["name"] == "本地受控"


def test_new_openai_compatible_provider_defaults_are_current(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "x")
    monkeypatch.setenv("KIMI_MODEL", "kimi-test")
    monkeypatch.setenv("ZHIPU_API_KEY", "x")
    monkeypatch.setenv("ZHIPU_MODEL", "glm-test")
    monkeypatch.setenv("TENCENT_TOKENHUB_API_KEY", "x")
    monkeypatch.setenv("HUNYUAN_MODEL", "hy-test")
    monkeypatch.setenv("QIANFAN_API_KEY", "x")
    monkeypatch.setenv("QIANFAN_MODEL", "ernie-test")
    registry = ProviderRegistry()
    assert registry.providers["kimi"].base_url == "https://api.moonshot.cn/v1"
    assert registry.providers["zhipu"].base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert registry.providers["hunyuan"].base_url == "https://tokenhub.tencentcloudmaas.com/v1"
    assert registry.providers["qianfan"].base_url == "https://qianfan.baidubce.com/v2"
    for key in ("kimi", "zhipu", "hunyuan", "qianfan"):
        assert registry.providers[key].info.configured is True
        assert registry.choose(key, []) is registry.providers[key]


def test_ai_picker_bootstraps_once_then_moves_to_the_composer():
    assert '/assets/provider-marketplace.css' in HTML
    assert '/assets/provider-marketplace.js' in HTML
    assert '/assets/fonts/noto-sans-sc/index.css' not in HTML
    # Keep one authoritative provider trigger in static HTML for bootstrap
    # resilience; customer-polish moves that same node into the live composer.
    assert HTML.count('id="providerBtn"') == 1
    assert 'id="providerTriggerName">自动选择<' in HTML
    assert "attachment.insertAdjacentElement('afterend', route)" in POLISH
    assert "route.dataset.surface = 'composer'" in POLISH
    assert "document.createElement('link')" not in POLISH
    assert "routeMaxWidth()" in POLISH


def test_picker_controls_the_existing_authoritative_provider_value():
    assert "new XMLHttpRequest()" in JS
    assert "'/api/providers'" in JS
    assert "select.value = provider.key" in JS
    assert "localStorage.setItem(STORAGE_KEY, selectedKey)" in JS
    for brand in ("openai", "anthropic", "gemini", "deepseek", "qwen", "doubao", "kimi", "zhipu", "hunyuan", "qianfan"):
        assert f"{brand}:" in JS
    assert ".ai-provider-card" in JS
    assert "尚未配置 API Key 和模型" in JS


def test_picker_keeps_the_workbench_compact_and_responsive():
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in CSS
    assert "@media(max-width:820px)" in CSS
    assert "grid-template-columns:1fr" in CSS
    assert "max-width:260px" in CSS
    assert "width:min(860px,94vw)" in CSS
    assert "if (window.innerWidth <= 820) return '132px'" in POLISH
    assert "if (window.innerWidth <= 520) return '116px'" in POLISH
