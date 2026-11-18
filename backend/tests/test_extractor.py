from app.services.image_extractor import (
    extract_from_css,
    extract_from_html,
    needs_browser,
    normalize_url,
)

PAGE = "https://shop.example.com/products/item.html"

RICH_HTML = """
<!doctype html>
<html><head>
  <title> Demo Shop </title>
  <meta property="og:image" content="/og.jpg">
  <meta name="twitter:image" content="https://cdn.example.com/twitter.png">
  <meta property="og:image:secure_url" content="/og.jpg">
  <link rel="icon" href="/favicon.ico">
  <link rel="stylesheet" href="/static/site.css">
  <link rel="preload" as="image" href="/hero-preload.webp">
  <style>.banner { background-image: url("../img/banner.png"); }</style>
  <script type="application/ld+json">{"@type": "Product", "image": ["/ld1.jpg", {"url": "/ld2.jpg"}]}</script>
</head><body>
  <img src="relative/a.jpg">
  <img src="//cdn.example.com/b.png#frag">
  <img src="data:image/gif;base64,R0lGOD" data-src="/lazy1.webp">
  <img data-lazy-src="/lazy2.jpg">
  <img data-original="/lazy3.gif">
  <img src="small.jpg" srcset="small.jpg 300w, large.jpg 1200w, medium.jpg 600w">
  <img src="a.jpg">
  <picture>
    <source type="image/avif" srcset="/pic.avif 1x, /pic@2x.avif 2x">
    <source type="image/webp" srcset="/pic.webp">
    <img src="/pic.jpg">
  </picture>
  <div style="background: url('/inline-bg.jpg') center/cover"></div>
  <div data-bg="/data-bg.jpg"></div>
  <video poster="/poster.jpg"></video>
  <svg><image href="/svg-embedded.png"/></svg>
  <a href="/full/photo.JPEG?size=xl">full size</a>
  <a href="/about">not an image</a>
  <img src="javascript:alert(1)">
  <img src="{{ item.image }}">
  <img src="">
</body></html>
"""


def urls(html: str, page: str = PAGE) -> list[str]:
    _, result = extract_from_html(html, page)
    return [c.url for c in result.candidates]


def test_extracts_all_sources() -> None:
    found = urls(RICH_HTML)
    expected = [
        "https://shop.example.com/og.jpg",
        "https://cdn.example.com/twitter.png",
        "https://shop.example.com/products/relative/a.jpg",
        "https://cdn.example.com/b.png",
        "https://shop.example.com/lazy1.webp",
        "https://shop.example.com/lazy2.jpg",
        "https://shop.example.com/lazy3.gif",
        "https://shop.example.com/products/large.jpg",
        "https://shop.example.com/products/a.jpg",
        "https://shop.example.com/pic@2x.avif",
        "https://shop.example.com/inline-bg.jpg",
        "https://shop.example.com/data-bg.jpg",
        "https://shop.example.com/poster.jpg",
        "https://shop.example.com/svg-embedded.png",
        "https://shop.example.com/full/photo.JPEG?size=xl",
        "https://shop.example.com/img/banner.png",
        "https://shop.example.com/ld1.jpg",
        "https://shop.example.com/ld2.jpg",
        "https://shop.example.com/favicon.ico",
        "https://shop.example.com/hero-preload.webp",
    ]
    assert set(found) == set(expected)
    assert len(found) == len(set(found)), "duplicates must be removed"


def test_srcset_and_picture_variants_are_collapsed() -> None:
    found = urls(RICH_HTML)
    assert "https://shop.example.com/products/small.jpg" not in found
    assert "https://shop.example.com/products/medium.jpg" not in found
    assert "https://shop.example.com/pic.webp" not in found
    assert "https://shop.example.com/pic.jpg" not in found


def test_metadata_and_stylesheets() -> None:
    _, result = extract_from_html(RICH_HTML, PAGE)
    assert result.title == "Demo Shop"
    assert result.stylesheet_urls == ["https://shop.example.com/static/site.css"]
    sources = {c.url: c.source for c in result.candidates}
    assert sources["https://shop.example.com/og.jpg"] == "og:image"
    assert sources["https://cdn.example.com/twitter.png"] == "twitter:image"


def test_base_href_is_respected() -> None:
    html = '<html><head><base href="https://static.example.org/assets/"></head><body><img src="x.png"></body></html>'
    assert urls(html) == ["https://static.example.org/assets/x.png"]


def test_external_css_resolves_relative_to_stylesheet() -> None:
    css = ".a{background:url(../img/bg.png)} @font-face{src:url(f.woff2)}"
    result = extract_from_css(css, "https://cdn.example.com/css/main.css")
    assert [c.url for c in result] == ["https://cdn.example.com/img/bg.png"]


def test_normalize_url() -> None:
    assert normalize_url(" HTTPS://Example.COM:443/a b.png#x ", PAGE) == "https://example.com/a%20b.png"
    assert normalize_url("http://example.com:8080/a.png", PAGE) == "http://example.com:8080/a.png"
    assert normalize_url("blob:https://x/y", PAGE) is None
    assert normalize_url("mailto:a@b.c", PAGE) is None


def test_needs_browser_for_spa_shell() -> None:
    soup, result = extract_from_html(
        '<html><body><div id="root"></div><script src="/app.js"></script></body></html>', PAGE
    )
    assert needs_browser(soup, result.candidates, 3) is not None


def test_needs_browser_false_for_rich_static_page() -> None:
    body = "".join(f'<p>Paragraph {i} with some text content.</p><img src="/img{i}.jpg">' for i in range(10))
    soup, result = extract_from_html(f"<html><body>{body}</body></html>", PAGE)
    assert needs_browser(soup, result.candidates, 3) is None
