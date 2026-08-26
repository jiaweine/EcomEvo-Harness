from __future__ import annotations

import os
import re
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("ECOMEVO_E2E_URL", "http://127.0.0.1:8765").rstrip("/")
ARTIFACT_DIR = Path(os.environ.get("ECOMEVO_E2E_ARTIFACT_DIR", "outputs/e2e"))
DESKTOP_VIEWPORT = {"width": 1920, "height": 1200}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
DEVICE_SCALE_FACTOR = 2
DESKTOP_CAPTURE = (DESKTOP_VIEWPORT["width"] * DEVICE_SCALE_FACTOR, DESKTOP_VIEWPORT["height"] * DEVICE_SCALE_FACTOR)
MOBILE_CAPTURE = (MOBILE_VIEWPORT["width"] * DEVICE_SCALE_FACTOR, MOBILE_VIEWPORT["height"] * DEVICE_SCALE_FACTOR)
CUSTOMER_BANNED_TERMS = (
    "Runtime",
    "Agent",
    "Plugin",
    "Provider",
    "Evidence",
    "Authority",
    "Provenance",
    "Event Trace",
    "Event Sourced",
    "认知引擎",
    "运行时插件",
    "执行轨迹",
)


def wait_server(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def capture(page, name: str) -> None:
    path = ARTIFACT_DIR / name
    page.screenshot(
        path=str(path),
        full_page=False,
        animations="disabled",
        caret="hide",
    )
    expected = MOBILE_CAPTURE if name == "product-mobile.png" else DESKTOP_CAPTURE
    with Image.open(path) as captured:
        assert captured.format == "PNG", (path, captured.format)
        assert captured.size == expected, (path, captured.size, expected)


def assert_customer_language(text: str, *, context: str) -> None:
    hits = [term for term in CUSTOMER_BANNED_TERMS if term in text]
    assert not hits, f"customer jargon leaked in {context}: {hits}"


def run() -> None:
    wait_server()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport=DESKTOP_VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="zh-CN",
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        try:
            page.goto(BASE_URL, wait_until="networkidle")
            expect(page.locator("#conversationTitle")).to_be_visible()
            expect(page.locator("#messageInput")).to_be_visible()
            expect(page.locator("#sceneEyebrow")).to_have_text("商品问题")

            # First-run help must explain customer actions, not the internal architecture.
            expect(page.locator("#productTour")).to_be_visible()
            expect(page.locator("#productTourTitle")).to_contain_text("问题和资料")
            page.locator("#messageInput").focus()
            expect(page.locator("#tourCloseBtn")).to_be_focused()
            assert_customer_language(page.locator("#productTour").inner_text(), context="product help")
            capture(page, "product-tour.png")
            page.keyboard.press("Control+K")
            expect(page.locator("#commandModal")).to_be_hidden()
            page.keyboard.press("Escape")
            expect(page.locator("#productTour")).to_be_hidden()
            expect(page.locator("body")).not_to_have_class(re.compile(r"\btour-open\b"))

            # The landing page is a customer service entry point, not a developer dashboard.
            expect(page.locator("#welcomePanel h2")).to_have_text("您好，今天想处理什么？")
            expect(page.locator(".ops-overview")).to_be_visible(timeout=10_000)
            expect(page.locator(".ops-overview")).to_contain_text("服务概况")
            expect(page.locator(".ops-overview .ops-metric")).to_have_count(4)
            expect(page.locator(".ops-overview")).to_contain_text("先确认再继续")
            expect(page.locator(".quick-card")).to_have_count(4)
            expect(page.locator(".agent-map-head")).to_contain_text("办理流程")
            assert_customer_language(page.locator("#welcomePanel").inner_text(), context="welcome workspace")
            design = page.evaluate(
                """() => {
                    const hero = document.querySelector('#welcomePanel h2');
                    const lead = document.querySelector('#welcomePanel .welcome-copy > p');
                    const cards = [...document.querySelectorAll('.quick-card')].filter(node => !node.hidden);
                    const route = [...document.querySelectorAll('.agent-route .route-node')];
                    const style = getComputedStyle(hero);
                    const firstCard = getComputedStyle(cards[0]);
                    return {
                        fontFamily: style.fontFamily,
                        fontSize: parseFloat(style.fontSize),
                        leadLength: lead.textContent.trim().length,
                        cardCount: cards.length,
                        maxCardCopy: Math.max(...cards.map(node => node.textContent.trim().length)),
                        routeCount: route.length,
                        cardRadius: parseFloat(firstCard.borderRadius),
                    };
                }"""
            )
            assert "sans-serif" in design["fontFamily"], design
            assert design["fontSize"] <= 40, design
            assert design["leadLength"] <= 70, design
            assert design["cardCount"] == 4, design
            assert design["maxCardCopy"] <= 40, design
            assert design["routeCount"] == 5, design
            assert design["cardRadius"] >= 8, design
            capture(page, "product-overview.png")

            # Processing choices use ordinary customer labels.
            page.locator("#providerBtn").click()
            expect(page.locator("#providerModal")).to_be_visible()
            expect(page.locator("#providerModalTitle")).to_have_text("选择处理方式")
            option_texts = page.locator("#providerSelect option").all_text_contents()
            assert option_texts and all(
                text.startswith("在线处理") or text.startswith("自动选择") or text.startswith("本地处理")
                for text in option_texts
            ), option_texts
            for title in page.locator("#providerGrid .provider-card b").all_text_contents():
                assert title.startswith("在线处理") or title.startswith("本地处理"), title
            assert_customer_language(page.locator("#providerModal").inner_text(), context="processing choices")
            page.locator("#providerModal .modal-close").click()

            # Service status remains truthful while hiding implementation vocabulary.
            page.locator("#settingsBtn").click()
            expect(page.locator("#runtimeModal")).to_be_visible()
            expect(page.locator("#providerModal")).to_be_hidden()
            expect(page.locator("#runtimeModalTitle")).to_have_text("服务状态")
            expect(page.locator("#runtimePluginGrid .runtime-plugin")).to_have_count(14, timeout=10_000)
            expect(page.locator("#runtimeSummary")).to_contain_text("服务正常")
            assert page.locator("#runtimePluginGrid .runtime-plugin-state.blocked").count() == 0
            assert page.locator("#runtimeLanes .runtime-lane").count() == 4
            assert_customer_language(page.locator("#runtimeModal").inner_text(), context="service status")
            capture(page, "product-plugins.png")
            page.locator("#runtimeCloseBtn").click()
            expect(page.locator("#runtimeModal")).to_be_hidden()
            expect(page.locator("#settingsBtn")).to_be_focused()

            # Scene changes keep customer wording even though internal scene keys are unchanged.
            page.locator('.scene[data-scene="merchant_review"]').click()
            expect(page.locator("#sceneEyebrow")).to_have_text("商家认证")
            capture(page, "product-scenes.png")

            first_prompt = "请帮我看看这个商家的主体和品牌授权是否齐全；如果缺材料，请直接告诉我还需要什么。"
            page.locator("#messageInput").fill(first_prompt)
            page.locator("#sendBtn").click()
            expect(page.locator(".msg.user .msg-content").filter(has_text=first_prompt)).to_be_visible()
            expect(page.locator(".msg.assistant")).to_have_count(1, timeout=45_000)
            expect(page.locator("#runtimePulse")).to_be_visible(timeout=10_000)
            expect(page.locator("#taskReadyChip")).not_to_contain_text("处理中")
            page.locator("#tab-progress").click()
            expect(page.locator("#panel-progress")).to_be_visible()
            expect(page.locator(".trace-ledger-head")).to_be_visible()
            expect(page.locator(".trace-ledger-head")).to_contain_text("办理进度")
            expect(page.locator(".trace-ledger-meta")).to_contain_text("已完成")
            capture(page, "product-runtime.png")

            page.locator("#tab-evidence").click()
            expect(page.locator("#panel-evidence")).to_be_visible()
            expect(page.locator("#panel-evidence .panel-copy")).to_contain_text("判断依据")
            evidence_cards = page.locator("#evidenceList .evidence-card")
            if evidence_cards.count():
                expect(page.locator("#panel-evidence .evidence-summary")).to_be_visible()
                expect(page.locator("#panel-evidence .evidence-summary-stat")).to_have_count(3)
                expect(page.locator("#panel-evidence .evidence-summary")).to_contain_text("资料总数")
                expect(evidence_cards.first.locator(".evidence-provenance-line")).to_be_visible()
                expect(evidence_cards.first.locator(".evidence-meta-item")).to_have_count(3)
                expect(page.locator("#tab-evidence .ops-tab-count")).to_have_text(str(evidence_cards.count()))
            assert_customer_language(page.locator("#panel-evidence").inner_text(), context="supporting information")
            capture(page, "product-evidence.png")
            page.locator("#tab-progress").click()

            action_cards = page.locator("#actionList .action-card")
            if action_cards.count():
                expect(action_cards.first.locator(".action-authority-row")).to_be_visible()
                expect(action_cards.first.locator(".evidence-audit-cell")).to_have_count(3)
                expect(action_cards.first.locator(".action-authority-row")).to_contain_text("需要确认")

            page.keyboard.press("Control+K")
            expect(page.locator("#commandModal")).to_be_visible()
            expect(page.locator("#commandInput")).to_be_focused()
            capture(page, "product-command.png")
            page.keyboard.press("Escape")
            expect(page.locator("#commandModal")).to_be_hidden()

            page.set_viewport_size(MOBILE_VIEWPORT)
            page.locator("#detailToggle").click()
            expect(page.locator("#rightbar")).to_have_class(re.compile(r"\bopen\b"))
            expect(page.locator(".right-title")).to_contain_text("办理详情")
            assert_customer_language(page.locator("#rightbar").inner_text(), context="mobile details")
            capture(page, "product-mobile.png")
            page.locator("#rightClose").click()
            expect(page.locator("#rightbar")).not_to_have_class(re.compile(r"\bopen\b"))

            # Same durable conversation in a second tab still reconciles correctly.
            page.set_viewport_size(DESKTOP_VIEWPORT)
            assert "conversation=" in page.url, page.url
            page2 = context.new_page()
            page2_errors: list[str] = []
            page2.on("pageerror", lambda exc: page2_errors.append(f"pageerror: {exc}"))
            page2.on("console", lambda msg: page2_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
            page2.goto(page.url, wait_until="networkidle")
            assistants_before = page2.locator(".msg.assistant").count()
            second_prompt = f"继续核对授权材料，标记 {time.time_ns()}"
            page2.locator("#messageInput").fill(second_prompt)
            page2.locator("#sendBtn").click()
            expect(page.locator(".msg.user .msg-content").filter(has_text=second_prompt)).to_be_visible(timeout=15_000)
            expect(page2.locator(".msg.assistant")).to_have_count(assistants_before + 1, timeout=45_000)
            expect(page.locator("#taskReadyChip")).not_to_contain_text("处理中", timeout=45_000)

            assert not browser_errors, browser_errors
            assert not page2_errors, page2_errors
        except Exception:
            page.screenshot(path=str(ARTIFACT_DIR / "failure.png"), full_page=True)
            raise
        finally:
            context.tracing.stop(path=str(ARTIFACT_DIR / "trace.zip"))
            browser.close()


if __name__ == "__main__":
    run()
    print("browser e2e ok")