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
DESKTOP_CAPTURE = (
    DESKTOP_VIEWPORT["width"] * DEVICE_SCALE_FACTOR,
    DESKTOP_VIEWPORT["height"] * DEVICE_SCALE_FACTOR,
)
MOBILE_CAPTURE = (
    MOBILE_VIEWPORT["width"] * DEVICE_SCALE_FACTOR,
    MOBILE_VIEWPORT["height"] * DEVICE_SCALE_FACTOR,
)
STARTED_AT = time.monotonic()


def checkpoint(label: str) -> None:
    print(f"[browser-e2e +{time.monotonic() - STARTED_AT:7.2f}s] {label}", flush=True)


def wait_server(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - only diagnostic on CI startup failure
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def capture(page, name: str) -> None:
    path = ARTIFACT_DIR / name
    page.screenshot(path=str(path), full_page=False, animations="disabled", caret="hide")
    expected = MOBILE_CAPTURE if name == "product-mobile.png" else DESKTOP_CAPTURE
    with Image.open(path) as captured:
        assert captured.format == "PNG", (path, captured.format)
        assert captured.size == expected, (path, captured.size, expected)


def run() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint("wait-server:start")
    wait_server()
    checkpoint("wait-server:ready")

    with sync_playwright() as p:
        checkpoint("chromium:launch:start")
        browser = p.chromium.launch()
        checkpoint("chromium:launch:ready")
        context = browser.new_context(
            viewport=DESKTOP_VIEWPORT,
            device_scale_factor=DEVICE_SCALE_FACTOR,
            locale="zh-CN",
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        browser_errors: list[str] = []
        conversation_gets: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: browser_errors.append(f"console: {msg.text}")
            if msg.type == "error"
            else None,
        )
        page.on(
            "request",
            lambda request: conversation_gets.append(request.url)
            if request.method == "GET" and "/api/conversations/" in request.url
            else None,
        )

        try:
            # This is a realtime application with WebSocket/reconciliation and supplementary
            # evidence refreshes. Readiness is the rendered workbench, not global network silence.
            checkpoint("initial-navigation:start")
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            checkpoint("initial-navigation:domcontentloaded")
            expect(page.locator("#conversationTitle")).to_be_visible(timeout=15_000)
            expect(page.locator("#messageInput")).to_be_visible()
            expect(page.locator("html")).to_have_attribute("data-ecomevo-theme", "customer-service")
            checkpoint("initial-ui:ready")

            # First-run help remains readable and modal-safe.
            expect(page.locator("#productTour")).to_be_visible()
            expect(page.locator("#productTourTitle")).to_have_text(
                "把问题和资料交给我们，处理过程会一直保留。"
            )
            page.locator("#messageInput").focus()
            expect(page.locator("#tourCloseBtn")).to_be_focused()
            capture(page, "product-tour.png")
            checkpoint("capture:product-tour")
            page.wait_for_timeout(1500)
            checkpoint(f"initial-conversation-get-count:{len(conversation_gets)}")
            page.keyboard.press("Control+K")
            expect(page.locator("#commandModal")).to_be_hidden()
            page.keyboard.press("Escape")
            expect(page.locator("#productTour")).to_be_hidden()

            # Empty state: calm AI entry, complete business coverage, no permanent inspector.
            expect(page.locator("#welcomePanel h2")).to_have_text("您好，今天想处理什么？")
            expect(page.locator("#welcomePanel .welcome-copy > p")).to_contain_text("下一步怎么做")
            expect(page.locator(".agent-map")).to_be_hidden()
            expect(page.locator("#rightbar")).not_to_have_class(re.compile(r"\bopen\b"))
            expect(page.locator(".ops-overview")).to_be_hidden(timeout=10_000)

            design = page.evaluate(
                """() => {
                    const hero = document.querySelector('#welcomePanel h2');
                    const lead = document.querySelector('#welcomePanel .welcome-copy > p');
                    const cards = [...document.querySelectorAll('.quick-card')].filter(node => !node.hidden);
                    const composer = document.querySelector('.composer');
                    const sidebar = document.querySelector('.leftbar');
                    const workspace = document.querySelector('.workspace');
                    const heroStyle = getComputedStyle(hero);
                    const bodyStyle = getComputedStyle(document.body);
                    return {
                        fontFamily: bodyStyle.fontFamily,
                        fontSize: parseFloat(heroStyle.fontSize),
                        leadLength: lead.textContent.trim().length,
                        cardCount: cards.length,
                        composerWidth: composer.getBoundingClientRect().width,
                        sidebarBackground: getComputedStyle(sidebar).backgroundColor,
                        workspaceBackground: getComputedStyle(workspace).backgroundColor,
                    };
                }"""
            )
            assert any(
                marker in design["fontFamily"]
                for marker in ("ui-sans-serif", "PingFang SC", "Segoe UI", "Microsoft YaHei", "Arial")
            ), design
            assert "Noto Sans" not in design["fontFamily"], design
            assert design["fontSize"] <= 36, design
            assert design["leadLength"] <= 60, design
            assert design["cardCount"] == 5, design
            assert design["composerWidth"] <= 900, design
            assert design["sidebarBackground"] in {
                "rgb(247, 247, 248)",
                "rgba(247, 247, 248, 1)",
                "rgb(248, 248, 250)",
                "rgba(248, 248, 250, 1)",
            }, design
            assert design["workspaceBackground"] in {
                "rgb(255, 255, 255)",
                "rgba(255, 255, 255, 1)",
            }, design
            capture(page, "product-overview.png")
            checkpoint("capture:product-overview")

            # Provider disclosure remains explicit.
            page.locator("#providerBtn").click()
            expect(page.locator("#providerModal")).to_be_visible()
            expect(page.locator("#providerModalTitle")).to_have_text("选择处理方式")
            expect(page.locator("#providerModal .modal-foot")).to_contain_text(
                "当前任务内容会按配置发送到对应服务"
            )
            page.locator("#providerModal .modal-close").click()

            # Runtime internals stay translated into customer-facing service language.
            page.locator("#settingsBtn").click()
            expect(page.locator("#runtimeModal")).to_be_visible()
            expect(page.locator("#runtimeModalTitle")).to_have_text("服务状态")
            expect(page.locator("#runtimeSummary")).to_contain_text("服务正常")
            expect(page.locator("#runtimePluginGrid .runtime-plugin")).to_have_count(14, timeout=10_000)
            expect(page.locator(".runtime-catalog")).to_be_hidden()
            capture(page, "product-plugins.png")
            checkpoint("capture:product-plugins")
            page.locator("#runtimeCloseBtn").click()
            expect(page.locator("#runtimeModal")).to_be_hidden()

            # Scene change reuses the empty task.
            page.locator('.scene[data-scene="merchant_review"]').click()
            expect(page.locator("#sceneEyebrow")).to_have_text("商家审核")
            capture(page, "product-scenes.png")
            checkpoint("capture:product-scenes")

            # Entering a real case promotes the UI into the contextual three-column workbench.
            first_prompt = "审核这个商家的主体和品牌授权；证据不足时明确告诉我还缺什么。"
            checkpoint("first-task:start")
            page.locator("#messageInput").fill(first_prompt)
            page.locator("#sendBtn").click()
            expect(page.locator(".msg.user .msg-content").filter(has_text=first_prompt)).to_be_visible()
            expect(page.locator(".msg.assistant")).to_have_count(1, timeout=45_000)
            expect(page.locator(".answer-provider").last).to_have_text("由 EcomEvo 完成")
            expect(page.locator("#runtimePulse")).to_be_visible(timeout=10_000)
            expect(page.locator("#taskReadyChip")).not_to_contain_text("处理中")
            checkpoint("first-task:complete")

            # Wide screens keep business context adjacent to the conversation.
            expect(page.locator("#rightbar")).to_be_visible()
            expect(page.locator("#detailToggle")).to_be_hidden()
            expect(page.locator("#rightClose")).to_be_hidden()
            expect(page.locator("#sceneEyebrow")).to_be_visible()
            expect(page.locator("#conversationTitle")).to_be_visible()
            page.locator("#tab-progress").click()
            expect(page.locator("#panel-progress")).to_be_visible()
            expect(page.locator(".trace-ledger-head")).to_be_visible()
            expect(page.locator(".trace-ledger-head")).to_contain_text("办理进度")
            capture(page, "product-runtime.png")
            checkpoint("capture:product-runtime")

            page.locator("#tab-evidence").click()
            expect(page.locator("#panel-evidence")).to_be_visible()
            evidence_cards = page.locator("#evidenceList .evidence-card")
            if evidence_cards.count():
                expect(page.locator("#panel-evidence .evidence-summary")).to_be_visible()
                expect(page.locator("#panel-evidence .evidence-summary-stat")).to_have_count(3)
                expect(evidence_cards.first.locator(".evidence-provenance-line")).to_be_visible()
            capture(page, "product-evidence.png")
            checkpoint("capture:product-evidence")
            page.locator("#tab-progress").click()

            # Keyboard command palette remains available over the workbench.
            page.keyboard.press("Control+K")
            expect(page.locator("#commandModal")).to_be_visible()
            expect(page.locator("#commandInput")).to_be_focused()
            capture(page, "product-command.png")
            checkpoint("capture:product-command")
            page.keyboard.press("Escape")
            expect(page.locator("#commandModal")).to_be_hidden()

            # Narrow screens collapse the persistent context back into a focus-managed drawer.
            page.set_viewport_size(MOBILE_VIEWPORT)
            expect(page.locator("#detailToggle")).to_be_visible()
            page.locator("#detailToggle").click()
            expect(page.locator("#rightbar")).to_have_class(re.compile(r"\bopen\b"))
            expect(page.locator("#rightClose")).to_be_focused()
            capture(page, "product-mobile.png")
            checkpoint("capture:product-mobile")
            page.locator("#rightClose").click()
            expect(page.locator("#rightbar")).not_to_have_class(re.compile(r"\bopen\b"))

            # Durable conversation still reconciles across tabs. The second tab also waits for
            # concrete UI readiness rather than global network silence.
            page.set_viewport_size(DESKTOP_VIEWPORT)
            assert "conversation=" in page.url, page.url
            page2 = context.new_page()
            page2_errors: list[str] = []
            page2.on("pageerror", lambda exc: page2_errors.append(f"pageerror: {exc}"))
            page2.on(
                "console",
                lambda msg: page2_errors.append(f"console: {msg.text}")
                if msg.type == "error"
                else None,
            )
            checkpoint("second-tab:navigation:start")
            page2.goto(page.url, wait_until="domcontentloaded", timeout=30_000)
            expect(page2.locator("#messageInput")).to_be_visible(timeout=15_000)
            expect(page2.locator(".msg.assistant")).to_have_count(1, timeout=15_000)
            checkpoint("second-tab:ready")
            assistants_before = page2.locator(".msg.assistant").count()
            second_prompt = f"跨标签页继续核对授权材料，标记 {time.time_ns()}"
            page2.locator("#messageInput").fill(second_prompt)
            page2.locator("#sendBtn").click()
            expect(page.locator(".msg.user .msg-content").filter(has_text=second_prompt)).to_be_visible(
                timeout=15_000
            )
            expect(page2.locator(".msg.assistant")).to_have_count(
                assistants_before + 1, timeout=45_000
            )
            expect(page.locator("#taskReadyChip")).not_to_contain_text("处理中", timeout=45_000)
            checkpoint("second-tab:reconciled")

            # Admin Test Center loads the packaged Gold Set without mutating production runtime state.
            eval_page = context.new_page()
            eval_errors: list[str] = []
            eval_page.on("pageerror", lambda exc: eval_errors.append(f"pageerror: {exc}"))
            eval_page.on(
                "console",
                lambda msg: eval_errors.append(f"console: {msg.text}")
                if msg.type == "error"
                else None,
            )
            checkpoint("evaluation-center:navigation:start")
            eval_page.goto(
                f"{BASE_URL}/api/runtime/evaluations/ui",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            expect(eval_page.locator("h1")).to_contain_text("发布前验证决策行为", timeout=10_000)
            expect(eval_page.locator("#caseCount")).to_have_text("9", timeout=10_000)
            expect(eval_page.locator("#runBtn")).to_be_visible()
            capture(eval_page, "evaluation-center.png")
            checkpoint("evaluation-center:ready")

            assert not browser_errors, browser_errors
            assert not page2_errors, page2_errors
            assert not eval_errors, eval_errors
            checkpoint("assertions:complete")
        except Exception:
            checkpoint("failure:capture")
            page.screenshot(path=str(ARTIFACT_DIR / "failure.png"), full_page=True)
            raise
        finally:
            checkpoint("trace:stop:start")
            context.tracing.stop(path=str(ARTIFACT_DIR / "trace.zip"))
            checkpoint("trace:stop:complete")
            browser.close()
            checkpoint("browser:closed")


if __name__ == "__main__":
    run()
    checkpoint("browser-e2e:ok")
