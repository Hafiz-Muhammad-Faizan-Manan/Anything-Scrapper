"""Extract, normalize and de-duplicate image URLs from HTML and CSS.

This module is pure (no network I/O) so it can be reused for both the static HTTPX path and
the Selenium-rendered DOM, and is straightforward to unit test.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, FeatureNotFound, Tag

from app.utils.helpers import best_srcset_url, css_image_urls, css_urls

logger = logging.getLogger(__name__)

# Attributes used by common lazy-loading libraries (lazysizes, lozad, jQuery lazyload, WP Rocket, ...).
LAZY_SRC_ATTRS = (
    "data-src",
    "data-lazy-src",
    "data-original",
    "data-lazy",
    "data-url",
    "data-hi-res-src",
    "data-full-src",
    "data-large-src",
    "data-img-src",
    "data-image",
    "data-echo",
    "data-actualsrc",
    "data-pagespeed-lazy-src",
    "data-orig-file",
    "data-large-file",
)
LAZY_SRCSET_ATTRS = ("data-srcset", "data-lazy-srcset", "data-original-set")
BG_ATTRS = ("data-bg", "data-background", "data-background-image", "data-bg-src", "data-bgset")

META_IMAGE_KEYS = {
    "og:image",
    "og:image:url",
    "og:image:secure_url",
    "twitter:image",
    "twitter:image:src",
    "msapplication-tileimage",
    "thumbnail",
}
ICON_RELS = {"icon", "shortcut icon", "apple-touch-icon", "apple-touch-icon-precomposed", "mask-icon", "image_src"}
JSONLD_IMAGE_KEYS = {"image", "thumbnailurl", "contenturl", "logo", "primaryimageofpage"}

IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|bmp|ico)(?:$|[?#])", re.IGNORECASE)
SKIP_PREFIXES = ("data:", "blob:", "javascript:", "about:", "mailto:", "tel:", "#")
SPA_ROOT_IDS = ("root", "__next", "app", "___gatsby", "svelte", "__nuxt", "main-app")
ENABLE_JS_RE = re.compile(r"(enable|turn on|requires?)\s+javascript|javascript\s+(is\s+)?(required|disabled)", re.I)
PATH_SAFE_CHARS = "/%:@!$&'()*+,;=-._~"


@dataclass(frozen=True)
class ImageCandidate:
    url: str
    source: str


@dataclass
class ExtractionResult:
    page_url: str
    base_url: str
    title: str | None
    candidates: list[ImageCandidate] = field(default_factory=list)
    stylesheet_urls: list[str] = field(default_factory=list)


def parse_html(markup: str | bytes) -> BeautifulSoup:
    try:
        return BeautifulSoup(markup, "lxml")
    except FeatureNotFound:  # pragma: no cover - lxml is a declared dependency
        return BeautifulSoup(markup, "html.parser")


def normalize_url(raw: str | None, base: str) -> str | None:
    """Resolve ``raw`` against ``base`` and canonicalize it. Returns None for unusable values."""
    if not raw:
        return None
    value = raw.strip().strip("\"'").strip()
    if not value or len(value) > 2048:
        return None
    lowered = value.lower()
    if lowered.startswith(SKIP_PREFIXES) or "{{" in value or "${" in value:
        return None
    try:
        absolute = urljoin(base, value)
        parts = urlsplit(absolute)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    path = quote(parts.path or "/", safe=PATH_SAFE_CHARS)
    query = quote(parts.query, safe=PATH_SAFE_CHARS + "?") if parts.query else ""
    return urlunsplit((scheme, netloc, path, query, ""))


def dedupe(candidates: Iterable[ImageCandidate]) -> list[ImageCandidate]:
    """Remove duplicate URLs, preserving first-seen order."""
    seen: set[str] = set()
    out: list[ImageCandidate] = []
    for c in candidates:
        if c.url not in seen:
            seen.add(c.url)
            out.append(c)
    return out


class _Collector:
    def __init__(self, base: str) -> None:
        self.base = base
        self.items: list[ImageCandidate] = []

    def add(self, raw: str | None, source: str, base: str | None = None) -> None:
        url = normalize_url(raw, base or self.base)
        if url:
            self.items.append(ImageCandidate(url=url, source=source))

    def add_srcset(self, value: str | None, source: str) -> None:
        if value:
            self.add(best_srcset_url(value), source)

    def add_css_value(self, value: str | None, source: str) -> None:
        if not value:
            return
        if "url(" in value or "image-set(" in value:
            for u in css_urls(value):
                self.add(u, source)
        elif source.endswith("attr"):
            # data-bg="..." style attributes may hold a bare URL or a srcset-like list
            first = best_srcset_url(value) if "," in value else value
            self.add(first, source)


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if isinstance(value, list):
        value = " ".join(value)
    return value if isinstance(value, str) else None


def _best_from_img(img: Tag, base: str) -> tuple[str, str] | None:
    """Pick the single most useful URL for an <img>: lazy srcset > srcset > lazy src > src."""
    for attr in LAZY_SRCSET_ATTRS:
        url = normalize_url(best_srcset_url(_attr(img, attr) or ""), base)
        if url:
            return url, "lazy-srcset"
    url = normalize_url(best_srcset_url(_attr(img, "srcset") or ""), base)
    if url:
        return url, "srcset"
    for attr in LAZY_SRC_ATTRS:
        url = normalize_url(_attr(img, attr), base)
        if url:
            return url, "lazy"
    url = normalize_url(_attr(img, "src"), base)
    if url:
        return url, "img"
    return None


def _best_from_source(source: Tag, base: str) -> tuple[str, str] | None:
    for attr in (*LAZY_SRCSET_ATTRS, "srcset"):
        url = normalize_url(best_srcset_url(_attr(source, attr) or ""), base)
        if url:
            return url, "picture"
    if (_attr(source, "type") or "").startswith("image/"):
        url = normalize_url(_attr(source, "src"), base)
        if url:
            return url, "picture"
    return None


def _iter_jsonld_images(node: object) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in JSONLD_IMAGE_KEYS:
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict) and isinstance(value.get("url"), str):
                    yield value["url"]
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            yield item
                        elif isinstance(item, dict) and isinstance(item.get("url"), str):
                            yield item["url"]
            if isinstance(value, (dict, list)):
                yield from _iter_jsonld_images(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_jsonld_images(item)


def extract_from_soup(soup: BeautifulSoup, page_url: str) -> ExtractionResult:
    base_url = page_url
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        resolved = normalize_url(_attr(base_tag, "href"), page_url)
        if resolved:
            base_url = resolved

    title = soup.title.get_text(strip=True) if soup.title else None
    c = _Collector(base_url)

    # 1. Metadata (og:image, twitter:image, itemprop=image) — usually the "hero" image.
    for meta in soup.find_all("meta"):
        key = (_attr(meta, "property") or _attr(meta, "name") or "").strip().lower()
        if key in META_IMAGE_KEYS:
            c.add(_attr(meta, "content"), key.split(":")[0] + ":image" if ":" in key else key)
        elif (_attr(meta, "itemprop") or "").lower() == "image":
            c.add(_attr(meta, "content"), "itemprop")

    # 2. <picture> elements: one image per picture (its sources are variants of the same image).
    handled_imgs: set[int] = set()
    for picture in soup.find_all("picture"):
        chosen: tuple[str, str] | None = None
        for source in picture.find_all("source"):
            chosen = _best_from_source(source, base_url)
            if chosen:
                break
        inner = picture.find("img")
        if isinstance(inner, Tag):
            handled_imgs.add(id(inner))
            if chosen is None:
                chosen = _best_from_img(inner, base_url)
        if chosen:
            c.items.append(ImageCandidate(url=chosen[0], source="picture"))

    # 3. <img> tags: the single best URL per element. A srcset lists size variants of the same
    # image, so only its largest candidate is kept; lazy-loading attributes win over
    # placeholder ``src`` values.
    for img in soup.find_all("img"):
        if id(img) in handled_imgs:
            continue
        chosen = _best_from_img(img, base_url)
        if chosen:
            c.items.append(ImageCandidate(url=chosen[0], source=chosen[1]))

    # Stand-alone <source type="image/..."> outside <picture>/<video>/<audio>.
    # (lxml does not treat <source> as a void element, so sources may nest: check all ancestors.)
    for source in soup.find_all("source"):
        if source.find_parent(["video", "audio", "picture"]) is not None:
            continue
        chosen = _best_from_source(source, base_url)
        if chosen:
            c.items.append(ImageCandidate(url=chosen[0], source="picture"))

    # 4. Other image-bearing elements.
    for video in soup.find_all("video", poster=True):
        c.add(_attr(video, "poster"), "video-poster")
    for inp in soup.find_all("input", type="image"):
        c.add(_attr(inp, "src"), "input")
    for svg_img in soup.find_all("image"):
        c.add(_attr(svg_img, "href") or _attr(svg_img, "xlink:href"), "svg-image")
    for obj in soup.find_all(["object", "embed"]):
        ref = _attr(obj, "data") or _attr(obj, "src")
        if ref and IMAGE_EXT_RE.search(ref):
            c.add(ref, "embed")
    for link in soup.find_all("link", href=True):
        rels = {r.lower() for r in (link.get("rel") or [])}
        rel_text = " ".join(sorted(rels))
        if (_attr(link, "as") or "").lower() == "image" and "preload" in rels:
            c.add_srcset(_attr(link, "imagesrcset"), "preload")
            c.add(_attr(link, "href"), "preload")
        elif rels & ICON_RELS or rel_text in ICON_RELS:
            c.add(_attr(link, "href"), "icon")
    for a in soup.find_all("a", href=True):
        href = _attr(a, "href")
        if href and IMAGE_EXT_RE.search(href):
            c.add(href, "link")

    # 5. Lazy background attributes and inline styles.
    for attr in BG_ATTRS:
        for el in soup.find_all(attrs={attr: True}):
            c.add_css_value(_attr(el, attr), "css-attr")
    for el in soup.find_all(style=True):
        style = _attr(el, "style") or ""
        for u in css_image_urls(style):
            c.add(u, "css-inline")

    # 6. <style> blocks.
    for style_tag in soup.find_all("style"):
        for u in css_image_urls(style_tag.get_text()):
            c.add(u, "css")

    # 7. JSON-LD structured data.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text() or "null")
        except (ValueError, TypeError):
            continue
        for u in _iter_jsonld_images(data):
            c.add(u, "json-ld")

    stylesheets: list[str] = []
    for link in soup.find_all("link", href=True):
        rels = {r.lower() for r in (link.get("rel") or [])}
        if "stylesheet" in rels:
            url = normalize_url(_attr(link, "href"), base_url)
            if url and url not in stylesheets:
                stylesheets.append(url)

    return ExtractionResult(
        page_url=page_url,
        base_url=base_url,
        title=title,
        candidates=dedupe(c.items),
        stylesheet_urls=stylesheets,
    )


def extract_from_html(markup: str | bytes, page_url: str) -> tuple[BeautifulSoup, ExtractionResult]:
    soup = parse_html(markup)
    return soup, extract_from_soup(soup, page_url)


def extract_from_css(css_text: str, stylesheet_url: str) -> list[ImageCandidate]:
    """Image URLs from an external stylesheet, resolved relative to the stylesheet itself."""
    out: list[ImageCandidate] = []
    for raw in css_image_urls(css_text):
        url = normalize_url(raw, stylesheet_url)
        if url:
            out.append(ImageCandidate(url=url, source="css"))
    return dedupe(out)


def candidates_from_urls(urls: Iterable[str], base: str, source: str) -> list[ImageCandidate]:
    out = []
    for raw in urls:
        url = normalize_url(raw, base)
        if url:
            out.append(ImageCandidate(url=url, source=source))
    return dedupe(out)


def needs_browser(soup: BeautifulSoup, candidates: list[ImageCandidate], min_images: int) -> str | None:
    """Decide whether the static HTML is incomplete and a headless browser render is warranted.

    Returns a human readable reason, or None when the static result looks complete.
    """
    body = soup.body
    body_text = body.get_text(" ", strip=True) if body else ""
    scripts = len(soup.find_all("script", src=True)) + len(soup.find_all("script", src=False))

    for root_id in SPA_ROOT_IDS:
        root = soup.find(id=root_id)
        if isinstance(root, Tag) and len(root.get_text(strip=True)) < 50 and not root.find("img"):
            return f"page looks like a JavaScript app (empty #{root_id} container)"
    if soup.find(attrs={"ng-app": True}) is not None and len(body_text) < 500:
        return "page looks like an AngularJS app"

    for noscript in soup.find_all("noscript"):
        if ENABLE_JS_RE.search(noscript.get_text(" ", strip=True)):
            return "page requires JavaScript"

    if len(body_text) < 200 and scripts >= 3:
        return "very little server-rendered content"

    page_images = [c for c in candidates if c.source not in ("og:image", "twitter:image", "icon", "itemprop")]
    if len(page_images) < min_images:
        return f"only {len(page_images)} image(s) found in static HTML"

    imgs = soup.find_all("img")
    if len(imgs) >= 4:
        placeholders = 0
        for img in imgs:
            src = (_attr(img, "src") or "").strip().lower()
            has_lazy = any(img.get(a) for a in LAZY_SRC_ATTRS + LAZY_SRCSET_ATTRS) or img.get("srcset")
            if (not src or src.startswith("data:")) and not has_lazy:
                placeholders += 1
        if placeholders / len(imgs) >= 0.5:
            return "most <img> tags have no real source (populated by JavaScript)"
    return None
