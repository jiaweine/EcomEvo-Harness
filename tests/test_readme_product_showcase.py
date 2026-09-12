from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
IMAGES = ROOT / "docs/images"


def test_readme_leads_with_product_language_not_engineering_deep_dive():
    first_screen = README[:4000].lower()
    for banned in (
        "engineering ·",
        "runtime architecture",
        "frozen kernel",
        "adaptive planning",
        "verifier",
        "plugin runtime",
        "belief state",
    ):
        assert banned not in first_screen
    assert "复杂电商业务，一次说清，持续处理" in README
    assert "适合这些电商场景" in README


def test_readme_uses_current_browser_e2e_product_captures():
    captures = {
        "product-customer-overview-v5.webp": (800, 500),
        "product-customer-evidence-v5.webp": (800, 500),
        "product-customer-mobile-v5.webp": (300, 600),
    }
    for name, minimum in captures.items():
        path = IMAGES / name
        assert path.is_file(), name
        with Image.open(path) as image:
            assert image.format == "WEBP", (name, image.format)
            width, height = image.size
        assert width >= minimum[0] and height >= minimum[1], (name, width, height)
        assert f"./docs/images/{name}" in README

    assert README.count("Browser E2E 实际页面") == 3
    for stale in (
        "./docs/images/product-customer-overview.png",
        "./docs/images/product-customer-evidence.png",
        "./docs/images/product-customer-mobile.png",
    ):
        assert stale not in README


def test_low_resolution_customer_thumbnail_stays_removed():
    assert not (IMAGES / "product-customer-overview.jpg").exists()
