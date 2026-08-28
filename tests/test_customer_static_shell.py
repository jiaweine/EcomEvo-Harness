from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
CUSTOMER_COPY = (ROOT / "frontend/customer-copy.js").read_text(encoding="utf-8")


class CustomerVisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "description":
            self.parts.append(values.get("content", ""))
        for name in ("aria-label", "placeholder", "title"):
            if values.get(name):
                self.parts.append(values[name])

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)


def customer_visible_text() -> str:
    parser = CustomerVisibleTextParser()
    parser.feed(HTML)
    return "\n".join(parser.parts)


def test_static_shell_is_customer_first_before_javascript_polish_runs():
    surface = customer_visible_text()
    for expected in (
        "EcomEvo 业务服务助手",
        "您好，今天想处理什么？",
        "处理方式",
        "办理详情",
        "判断依据",
        "待您确认",
        "系统状态",
    ):
        assert expected in surface


def test_static_shell_does_not_expose_internal_runtime_vocabulary():
    surface = customer_visible_text().lower()
    for banned in (
        "decision runtime",
        "runtime 插件",
        "plugin runtime",
        "event sourced",
        "evidence & authority",
        "provider",
        "planner",
        "verifier",
        "provenance",
        "sandbox",
        "harness",
        "认知引擎",
        "多模态输入",
        "任务控制面",
        "执行控制",
    ):
        assert banned.lower() not in surface


def test_customer_shell_keeps_business_action_confirmation_boundary_explicit():
    surface = customer_visible_text()
    assert "涉及真实业务变更时，会先请您确认" in surface
    assert "需要改变真实业务状态的操作不会自动执行" in surface


def test_customer_copy_enhancer_does_not_rewrite_static_shell_language():
    for static_selector in (
        "#productGuideBtn em",
        "#providerBtn em",
        "#settingsBtn",
        "#welcomePanel h2",
        ".right-title b",
        "#providerModal .modal-foot span",
    ):
        assert static_selector not in CUSTOMER_COPY


def test_external_service_disclosure_survives_customer_enhancement():
    assert "选择外部服务时，当前任务内容会按配置发送到对应服务" in HTML
    assert "部分处理方式可能会按当前配置使用已接入的外部服务" not in CUSTOMER_COPY
