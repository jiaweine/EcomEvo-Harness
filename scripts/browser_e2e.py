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


def run() -> None:
    wait_server()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Logical viewport stays deterministic for interaction assertions while DPR=2
        # produces 3840x2400 desktop captures suitable for README/source inspection.
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
            expect(page.locator("#sceneEyebrow")).to_have_text("商品治理")
            capture(page, "product-overview.png")

            page.locator("#providerBtn").click()
            expect(page.locator("#providerModal")).to_be_visible()
            option_texts = page.locator("#providerSelect option").all_text_contents()
            assert option_texts and all(
                text.startswith("认知引擎") or text.startswith("自动编排") or text.startswith("本地受控")
                for text in option_texts
            ), option_texts
            for title in page.locator("#providerGrid .provider-card b").all_text_contents():
                assert title.startswith("认知引擎") or title.startswith("本地受控"), title
            page.locator("#providerModal .modal-close").click()

            # Empty-task scene changes should reuse the current task rather than creating junk.
            page.locator('.scene[data-scene="merchant_review"]').click()
            expect(page.locator("#sceneEyebrow")).to_have_text("商家审核")
            capture(page, "product-scenes.png")

            first_prompt = "审核这个商家的主体和品牌授权；证据不足时明确告诉我还缺什么。"
            page.locator("#messageInput").fill(first_prompt)
            page.locator("#sendBtn").click()
            expect(page.locator(".msg.user .msg-content").filter(has_text=first_prompt)).to_be_visible()
            expect(page.locator(".msg.assistant")).to_have_count(1, timeout=45_000)
            expect(page.locator("#runtimePulse")).to_be_visible(timeout=10_000)
            expect(page.locator("#taskReadyChip")).not_to_contain_text("处理中")
            page.locator("#tab-progress").click()
            expect(page.locator("#panel-progress")).to_be_visible()
            capture(page, "product-runtime.png")

            page.locator("#tab-evidence").click()
            expect(page.locator("#panel-evidence")).to_be_visible()
            capture(page, "product-evidence.png")
            page.locator("#tab-progress").click()

            page.keyboard.press("Control+K")
            expect(page.locator("#commandModal")).to_be_visible()
            expect(page.locator("#commandInput")).to_be_focused()
            capture(page, "product-command.png")
            page.keyboard.press("Escape")
            expect(page.locator("#commandModal")).to_be_hidden()

            page.set_viewport_size(MOBILE_VIEWPORT)
            page.locator("#detailToggle").click()
            expect(page.locator("#rightbar")).to_have_class(re.compile(r"\bopen\b"))
            capture(page, "product-mobile.png")
            page.locator("#rightClose").click()
            expect(page.locator("#rightbar")).not_to_have_class(re.compile(r"\bopen\b"))

            # Same durable conversation in a second tab: remote accepted user message must
            # reconcile into the first tab before/alongside its answer.
            page.set_viewport_size(DESKTOP_VIEWPORT)
            assert "conversation=" in page.url, page.url
            page2 = context.new_page()
            page2_errors: list[str] = []
            page2.on("pageerror", lambda exc: page2_errors.append(f"pageerror: {exc}"))
            page2.on("console", lambda msg: page2_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
            page2.goto(page.url, wait_until="networkidle")
            assistants_before = page2.locator(".msg.assistant").count()
            second_prompt = f"跨标签页继续核对授权材料，标记 {time.time_ns()}"
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
