from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("ECOMEVO_GALLERY_URL", "http://127.0.0.1:8765").rstrip("/")
OUT_DIR = Path(os.environ.get("ECOMEVO_GALLERY_DIR", "docs/images/real"))
DESKTOP = {"width": 1920, "height": 1200}
MOBILE = {"width": 390, "height": 844}


def wait_server():
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("EcomEvo server unavailable")


def capture(page, name):
    path = OUT_DIR / name
    page.screenshot(path=str(path), animations="disabled", caret="hide")
    return path


def verify(path, size):
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == size, (path, image.size)


def run():
    wait_server()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sample = OUT_DIR.parent / "merchant-evidence.txt"
    sample.write_text(
        "商家主体：上海示例贸易有限公司\n"
        "品牌授权：有效\n"
        "风险记录：无重大处罚\n",
        encoding="utf-8",
    )

    desktop = []
    mobile = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        errors = []

        context = browser.new_context(viewport=DESKTOP, device_scale_factor=2, locale="zh-CN")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        page.goto(BASE_URL, wait_until="networkidle")
        expect(page.locator("#messageInput")).to_be_visible()

        page.locator('.scene[data-scene="merchant_review"]').click()
        page.locator("#fileInput").set_input_files(str(sample))
        expect(page.locator("#assetCountChip")).to_have_text("1", timeout=20000)

        page.locator("#messageInput").fill("审核这个商家的主体、品牌授权和历史风险，并给出关键依据和下一步建议。")
        page.locator("#sendBtn").click()
        expect(page.locator(".msg.assistant")).to_have_count(1, timeout=45000)

        desktop.append(capture(page, "product-overview.png"))

        page.locator("#tab-evidence").click()
        expect(page.locator("#panel-evidence")).to_be_visible()
        desktop.append(capture(page, "product-evidence.png"))

        try:
            page.locator("#tab-action").click()
            expect(page.locator("#panel-action")).to_be_visible()
            desktop.append(capture(page, "product-action.png"))
        except Exception:
            pass

        page.keyboard.press("Control+K")
        expect(page.locator("#commandModal")).to_be_visible()
        desktop.append(capture(page, "product-command.png"))

        context.close()
        context = browser.new_context(viewport=MOBILE, device_scale_factor=2, locale="zh-CN")
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle")
        expect(page.locator("#messageInput")).to_be_visible()
        mobile.append(capture(page, "product-mobile-workbench.png"))

        if errors:
            raise AssertionError(errors)
        browser.close()

    for image in desktop:
        verify(image, (3840, 2400))
    for image in mobile:
        verify(image, (780, 1688))

    sample.unlink(missing_ok=True)


if __name__ == "__main__":
    run()
