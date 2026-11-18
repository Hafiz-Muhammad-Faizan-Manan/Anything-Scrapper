"""Small, dependency-free helpers shared across services."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import secrets
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

T = TypeVar("T")

# --------------------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------------------

ID_PATTERN = r"^[a-f0-9]{16}$"
ID_RE = re.compile(ID_PATTERN)


def new_id() -> str:
    """Return a random 16-char hex id (64 bits). Ids are only ever generated server side."""
    return secrets.token_hex(8)


# --------------------------------------------------------------------------------------
# Image types
# --------------------------------------------------------------------------------------

IMAGE_MIME_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "ico": "image/x-icon",
}

MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/apng": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "image/x-ms-bmp": "bmp",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}

# Formats that are already compressed; deflating them in a ZIP only wastes CPU.
COMPRESSED_EXTS = {"jpg", "png", "gif", "webp", "avif"}

_SVG_ROOT_RE = re.compile(rb"<svg[\s>/]", re.IGNORECASE)


def sniff_image_type(data: bytes) -> str | None:
    """Detect an image type from its leading bytes. Returns an extension key or None."""
    head = data[:64]
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        brands = head[8:40]
        if b"avif" in brands or b"avis" in brands:
            return "avif"
        return None  # HEIC and other ISO-BMFF containers are not supported by browsers
    if head[:2] == b"BM" and len(data) > 26:
        return "bmp"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    # SVG is text: allow BOM / whitespace / XML declaration / comments / doctype before <svg
    text_head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if text_head.startswith(b"<") and _SVG_ROOT_RE.search(text_head):
        lowered = text_head[:512].lower()
        if b"<html" not in lowered:
            return "svg"
    return None


def ext_from_mime(content_type: str | None) -> str | None:
    if not content_type:
        return None
    mime = content_type.split(";", 1)[0].strip().lower()
    return MIME_TO_EXT.get(mime)


# --------------------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------------------

_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_KNOWN_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|bmp|ico|tiff?|heic)$", re.IGNORECASE)
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_FILENAME_STEM = 80


def safe_filename(url: str, ext: str, taken: set[str] | None = None) -> str:
    """Build a filesystem- and ZIP-safe filename for an image.

    The stem comes from the last URL path segment, stripped of anything that is not
    ``[A-Za-z0-9._-]``. The extension is always the *detected* type, never the one from the
    URL. ``taken`` (case-insensitive) is updated so names are unique within a job.
    """
    path = urlsplit(url).path
    base = unquote(os.path.basename(path.rstrip("/"))) if path else ""
    base = _KNOWN_EXT_RE.sub("", base)
    stem = _UNSAFE_CHARS_RE.sub("-", base).strip(".-_")
    stem = re.sub(r"-{2,}", "-", stem)[:MAX_FILENAME_STEM].strip(".-_")
    if not stem or stem.lower() in _WINDOWS_RESERVED:
        stem = "image"

    candidate = f"{stem}.{ext}"
    if taken is not None:
        n = 2
        while candidate.lower() in taken:
            candidate = f"{stem}-{n}.{ext}"
            n += 1
        taken.add(candidate.lower())
    return candidate


# --------------------------------------------------------------------------------------
# srcset / CSS parsing
# --------------------------------------------------------------------------------------


def parse_srcset(value: str) -> list[tuple[str, float]]:
    """Parse a srcset attribute into ``[(url, weight)]``.

    Follows the WHATWG algorithm closely enough to cope with URLs that contain commas
    (common with image CDNs, e.g. ``/w_200,h_100/a.jpg``). ``weight`` is the ``w`` value or
    the ``x`` density scaled by 1000, so the largest candidate sorts last.
    """
    results: list[tuple[str, float]] = []
    s = value or ""
    i, n = 0, len(s)
    while i < n:
        while i < n and (s[i].isspace() or s[i] == ","):
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not s[i].isspace():
            i += 1
        url = s[start:i]
        descriptor = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            depth = 0
            dstart = i
            while i < n:
                ch = s[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth = max(0, depth - 1)
                elif ch == "," and depth == 0:
                    break
                i += 1
            descriptor = s[dstart:i].strip()
            i += 1  # skip comma
        if not url:
            continue
        weight = 1000.0
        for token in descriptor.split():
            try:
                if token.endswith("w"):
                    weight = float(token[:-1])
                elif token.endswith("x"):
                    weight = float(token[:-1]) * 1000
            except ValueError:
                pass
        results.append((url, weight))
    return results


def best_srcset_url(value: str) -> str | None:
    candidates = parse_srcset(value)
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[1])[0]


_CSS_URL_RE = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]+))\s*\)""", re.IGNORECASE)
_CSS_IMAGE_SET_RE = re.compile(r"""(?:-webkit-)?image-set\(([^;{}]*)\)""", re.IGNORECASE)
_CSS_QUOTED_RE = re.compile(r""""([^"]+)"|'([^']+)'""")
_CSS_IMAGE_DECL_RE = re.compile(
    r"""(?:^|[;{\s])(background(?:-image)?|list-style(?:-image)?|border-image(?:-source)?|"""
    r"""mask(?:-image)?|-webkit-mask(?:-image)?|content|cursor)\s*:\s*([^;{}]+)""",
    re.IGNORECASE,
)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def css_urls(value: str) -> list[str]:
    """Return all ``url(...)`` and ``image-set("...")`` references in a CSS value."""
    found: list[str] = []
    for m in _CSS_URL_RE.finditer(value):
        url = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if url:
            found.append(url)
    for m in _CSS_IMAGE_SET_RE.finditer(value):
        inner = _CSS_URL_RE.sub("", m.group(1))  # url() forms already captured above
        for q in _CSS_QUOTED_RE.finditer(inner):
            url = (q.group(1) or q.group(2) or "").strip()
            if url:
                found.append(url)
    return found


def css_image_urls(css_text: str) -> list[str]:
    """Return image URLs referenced from image-bearing declarations in a stylesheet.

    Only declarations such as ``background``/``background-image``/``list-style-image`` are
    considered, so ``@font-face { src: url(font.woff2) }`` and ``@import`` are ignored.
    """
    text = _CSS_COMMENT_RE.sub("", css_text)
    urls: list[str] = []
    for m in _CSS_IMAGE_DECL_RE.finditer(text):
        urls.extend(css_urls(m.group(2)))
    return urls


# --------------------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------------------


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retries: int,
    backoff: float,
    should_retry: Callable[[BaseException], bool],
    description: str = "operation",
) -> T:
    """Run ``fn`` retrying on errors accepted by ``should_retry`` with jittered exponential backoff."""
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below when not retryable
            if attempt >= retries or not should_retry(exc):
                raise
            delay = backoff * (2**attempt) * (0.75 + random.random() / 2)
            attempt += 1
            logger.debug("Retrying %s after %r (attempt %d, sleeping %.2fs)", description, exc, attempt, delay)
            await asyncio.sleep(delay)


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"
