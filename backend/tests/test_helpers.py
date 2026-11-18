from app.utils.helpers import (
    best_srcset_url,
    css_image_urls,
    parse_srcset,
    safe_filename,
    sniff_image_type,
)
from tests.conftest import SVG, make_image


def test_parse_srcset_with_commas_in_urls() -> None:
    value = "https://cdn.x/w_200,h_100/a.jpg 200w, https://cdn.x/w_800,h_400/a.jpg 800w"
    assert parse_srcset(value) == [
        ("https://cdn.x/w_200,h_100/a.jpg", 200.0),
        ("https://cdn.x/w_800,h_400/a.jpg", 800.0),
    ]


def test_best_srcset_density() -> None:
    assert best_srcset_url("a.png, b.png 2x, c.png 1.5x") == "b.png"
    assert best_srcset_url("") is None


def test_css_image_urls_skips_fonts_and_imports() -> None:
    css = """
    @import url("other.css");
    @font-face { font-family: X; src: url(font.woff2) format("woff2"); }
    .hero { background: #000 url('hero.jpg') no-repeat; }
    .icon { background-image: image-set("icon.png" 1x, "icon@2x.png" 2x); }
    /* .old { background: url(commented.png) } */
    ul { list-style-image: url(bullet.svg) }
    """
    assert css_image_urls(css) == ["hero.jpg", "icon.png", "icon@2x.png", "bullet.svg"]


def test_sniff_image_types() -> None:
    assert sniff_image_type(make_image("PNG")) == "png"
    assert sniff_image_type(make_image("JPEG")) == "jpg"
    assert sniff_image_type(make_image("GIF")) == "gif"
    assert sniff_image_type(make_image("WEBP")) == "webp"
    assert sniff_image_type(SVG) == "svg"
    assert sniff_image_type(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1") == "avif"
    assert sniff_image_type(b"<!doctype html><html><body>404</body></html>") is None
    assert sniff_image_type(b"<html><svg></svg></html>") is None
    assert sniff_image_type(b"GIF") is None


def test_safe_filename() -> None:
    taken: set[str] = set()
    assert safe_filename("https://x.com/a/My%20Photo%20(1).JPG?x=1", "jpg", taken) == "My-Photo-1.jpg"
    # Collisions are case-insensitive (Windows/macOS filesystems, ZIP extractors).
    assert safe_filename("https://y.com/b/my photo 1.png", "jpg", taken) == "my-photo-1-2.jpg"
    assert safe_filename("https://x.com/a/My%20Photo%20(1).jpg", "jpg", taken) == "My-Photo-1-3.jpg"
    # Path traversal and reserved names are neutralized; extension follows the detected type.
    assert safe_filename("https://x.com/..%2F..%2Fetc%2Fpasswd", "png") == "etc-passwd.png"
    assert safe_filename("https://x.com/", "webp") == "image.webp"
    assert safe_filename("https://x.com/CON.png", "png") == "image.png"
    assert safe_filename("https://x.com/script.php", "gif") == "script.php.gif"
