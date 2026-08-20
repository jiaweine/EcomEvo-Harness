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
DPR = 2
DESKTOP_SIZE = (3840, 2400)
MOBILE_SIZE = (780, 1688)
TOUR_KEY = "ecomevo.product-tour.v1"


def wait_server(timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - diagnostic path for CI
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"EcomEvo did not become healthy: {last_error}")


def capture(page, name: str) -> Path:
    path = OUT_DIR / name
    page.screenshot(path=str(path), full_page=False, animations="disabled", caret="hide")
    return path


def assert_png(path: Path, expected: tuple[int, int]) -> None:
    with Image.open(path) as image:
        actual = image.size
        if actual != expected:
            raise AssertionError(f"{path}: expected {expected}, got {actual}")
        if image.format != "PNG":
            raise AssertionError(f"{path}: expected PNG, got {image.format}")
    print(f"gallery: {path} {actual[0]}x{actual[1]} {path.stat().st_size} bytes")


def run() -> None:
    wait_server()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sample = OUT_DIR.parent / "readme-merchant-evidence.txt"
    sample.write_text(
        "商家主体：上海示例贸易有限公司\n"
        "统一社会信用代码：91310000MA1EXAMPLE\n"
        "品牌授权：EcomEvo Demo Brand，授权链完整，有效期至 2027-12-31\n"
        "经营范围：日用百货、电子产品零售\n"
        "历史风险：近 12 个月无重大处罚记录\n",
        encoding="utf-8",
    )

    desktop_paths: list[Path] = []
    mobile_paths: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=DESKTOP, device_scale_factor=DPR, locale="zh-CN")
        context.add_init_script(f"localStorage.setItem('{TOUR_KEY}', 'seen')")
        page = context.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        page.goto(BASE_URL, wait_until="networkidle")
        expect(page.locator("#conversationTitle")).to_be_visible()
        expect(page.locator("#messageInput")).to_be_visible()
        expect(page.locator("#sceneEyebrow")).to_have_text("商品治理")
        expect(page.locator("#welcomePanel")).to_be_visible()
        desktop_paths.append(capture(page, "product-overview.png"))

        page.locator('.scene[data-scene="merchant_review"]').click()
        expect(page.locator("#sceneEyebrow")).to_have_text("商家审核")
        desktop_paths.append(capture(page, "product-scenes.png"))

        page.locator("#fileInput").set_input_files(str(sample))
        expect(page.locator("#assetCountChip")).to_have_text("1", timeout=20_000)
        try:
            page.locator("#providerSelect").select_option("demo")
        except Exception:
            page.locator("#providerSelect").select_option("auto")

        prompt = "审核这个商家的主体、品牌授权和历史风险，给出通过、补件或拒绝建议，并列出关键依据。"
        page.locator("#messageInput").fill(prompt)
        page.locator("#sendBtn").click()
        expect(page.locator(".msg.user .msg-content").filter(has_text=prompt)).to_be_visible()
        expect(page.locator(".msg.assistant")).to_have_count(1, timeout=45_000)
        expect(page.locator("#taskReadyChip")).not_to_contain_text("处理中", timeout=45_000)

        page.locator("#tab-evidence").click()
        expect(page.locator("#panel-evidence")).to_be_visible()
        desktop_paths.append(capture(page, "product-evidence.png"))

        page.keyboard.press("Control+K")
        expect(page.locator("#commandModal")).to_be_visible()
        expect(page.locator("#commandInput")).to_be_focused()
        desktop_paths.append(capture(page, "product-command.png"))
        page.keyboard.press("Escape")
        expect(page.locator("#commandModal")).to_be_hidden()

        page.set_viewport_size(MOBILE)
        page.reload(wait_until="networkidle")
        expect(page.locator("#messageInput")).to_be_visible()
        expect(page.locator(".msg.assistant")).to_have_count(1, timeout=20_000)
        mobile_paths.append(capture(page, "product-mobile-workbench.png"))

        page.locator("#detailToggle").click()
        expect(page.locator("#rightbar")).to_have_class(lambda value: bool(value and "open" in value.split()))
        mobile_paths.append(capture(page, "product-mobile-detail.png"))

        page.locator("#tab-evidence").click()
        expect(page.locator("#panel-evidence")).to_be_visible()
        mobile_paths.append(capture(page, "product-mobile-evidence.png"))

        if page_errors:
            raise AssertionError(f"browser page errors: {page_errors}")
        browser.close()

    for path in desktop_paths:
        assert_png(path, DESKTOP_SIZE)
    for path in mobile_paths:
        assert_png(path, MOBILE_SIZE)
    sample.unlink(missing_ok=True)


if __name__ == "__main__":
    run()
    print("README gallery capture ok")
