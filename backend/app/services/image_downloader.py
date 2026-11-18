"""Concurrent, validated image downloading into a per-job on-disk cache."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from app.config import Settings
from app.services.http_scraper import IMAGE_ACCEPT, FetchError, HttpScraper
from app.services.image_extractor import ImageCandidate
from app.utils.helpers import IMAGE_MIME_TYPES, new_id, safe_filename, sniff_image_type

logger = logging.getLogger(__name__)

ACCEPTABLE_NON_IMAGE_TYPES = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
    "application/binary",
    "application/x-binary",
    "text/plain",  # some servers mislabel SVG/ICO; verified by magic bytes anyway
    "text/xml",
    "application/xml",
}

_SVG_DIM_RE = re.compile(r"""\b(width|height)\s*=\s*["']\s*([\d.]+)\s*(px)?\s*["']""", re.IGNORECASE)
_SVG_VIEWBOX_RE = re.compile(
    r"""viewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*["']""", re.IGNORECASE
)
_SVG_TAG_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)

ProgressCallback = Callable[[int, int], Awaitable[None] | None]


class ImageRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class StoredImage:
    id: str
    url: str
    source: str
    extension: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    sha256: str
    path: Path
    filename: str = ""


@dataclass
class DownloadReport:
    images: list[StoredImage]
    failures: Counter[str]
    attempted: int
    limit_reached: bool = False


def _content_type_check(headers: httpx.Headers) -> None:
    ct = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ct.startswith("image/") or ct in ACCEPTABLE_NON_IMAGE_TYPES:
        return
    raise FetchError(f"Not an image (Content-Type: {ct or 'unknown'}).", kind="rejected")


def svg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    text = data[:8192].decode("utf-8", errors="ignore")
    tag = _SVG_TAG_RE.search(text)
    if not tag:
        return None, None
    root = tag.group(0)
    dims: dict[str, float] = {}
    for name, value, _ in _SVG_DIM_RE.findall(root):
        try:
            dims[name.lower()] = float(value)
        except ValueError:
            pass
    if "width" in dims and "height" in dims:
        return round(dims["width"]) or None, round(dims["height"]) or None
    vb = _SVG_VIEWBOX_RE.search(root)
    if vb:
        try:
            return round(float(vb.group(1))) or None, round(float(vb.group(2))) or None
        except ValueError:
            pass
    return None, None


def inspect_image(data: bytes, ext: str) -> tuple[int | None, int | None]:
    """Verify image bytes with Pillow (raster) and return ``(width, height)``.

    Raises :class:`ImageRejected` when the data is not a decodable image.
    """
    if ext == "svg":
        return svg_dimensions(data)
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            im.verify()
        return width, height
    except Image.DecompressionBombError as exc:
        raise ImageRejected("too_large") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        if ext == "avif":
            # Pillow may lack AVIF support on some builds; accept on magic bytes alone.
            return None, None
        raise ImageRejected("corrupt") from exc


class ImageDownloader:
    def __init__(self, settings: Settings, http: HttpScraper) -> None:
        self.settings = settings
        self.http = http
        Image.MAX_IMAGE_PIXELS = settings.max_image_pixels

    async def download_all(
        self,
        candidates: list[ImageCandidate],
        *,
        referer: str,
        job_dir: Path,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadReport:
        job_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(self.settings.max_concurrent_downloads)
        max_images = self.settings.max_images_per_job
        to_try = candidates[: max_images * 2]
        failures: Counter[str] = Counter()
        results: dict[int, StoredImage] = {}
        state = {"processed": 0, "bytes": 0, "stored": 0, "limit": False}
        total = len(to_try)

        async def report() -> None:
            if on_progress is not None:
                res = on_progress(state["processed"], total)
                if inspect.isawaitable(res):
                    await res

        async def worker(index: int, candidate: ImageCandidate) -> None:
            try:
                async with semaphore:
                    if state["stored"] >= max_images or state["bytes"] >= self.settings.max_job_total_bytes:
                        state["limit"] = True
                        failures["limit"] += 1
                        return
                    stored = await self._download_one(candidate, referer, job_dir)
                    if state["bytes"] + stored.size_bytes > self.settings.max_job_total_bytes:
                        stored.path.unlink(missing_ok=True)
                        state["limit"] = True
                        failures["limit"] += 1
                        return
                    state["bytes"] += stored.size_bytes
                    state["stored"] += 1
                    results[index] = stored
            except ImageRejected as exc:
                failures[exc.reason] += 1
            except FetchError as exc:
                failures[exc.kind] += 1
                logger.debug("Image %s failed: %s", candidate.url, exc.message)
            except Exception:  # noqa: BLE001 - one bad image must never fail the job
                failures["error"] += 1
                logger.exception("Unexpected error downloading %s", candidate.url)
            finally:
                state["processed"] += 1
                await report()

        await asyncio.gather(*(worker(i, c) for i, c in enumerate(to_try)))

        # Content de-duplication in page order (same bytes served from different URLs).
        ordered: list[StoredImage] = []
        seen_hashes: set[str] = set()
        taken_names: set[str] = set()
        for index in sorted(results):
            img = results[index]
            if img.sha256 in seen_hashes:
                failures["duplicate"] += 1
                img.path.unlink(missing_ok=True)
                continue
            if len(ordered) >= max_images:
                # Downloads already in flight when the cap was reached.
                failures["limit"] += 1
                state["limit"] = True
                img.path.unlink(missing_ok=True)
                continue
            seen_hashes.add(img.sha256)
            img.filename = safe_filename(img.url, img.extension, taken_names)
            ordered.append(img)

        return DownloadReport(images=ordered, failures=failures, attempted=total, limit_reached=bool(state["limit"]))

    async def _download_one(self, candidate: ImageCandidate, referer: str, job_dir: Path) -> StoredImage:
        result = await self.http.fetch(
            candidate.url,
            max_bytes=self.settings.max_image_bytes,
            accept=IMAGE_ACCEPT,
            referer=referer,
            header_check=_content_type_check,
        )
        data = result.content
        if not data:
            raise ImageRejected("empty")
        ext = sniff_image_type(data)
        if ext is None:
            raise ImageRejected("not_image")

        width, height = await asyncio.to_thread(inspect_image, data, ext)
        min_dim = self.settings.min_image_dimension
        if width is not None and height is not None and width < min_dim and height < min_dim:
            raise ImageRejected("tiny")

        image_id = new_id()
        path = job_dir / f"{image_id}.{ext}"
        await asyncio.to_thread(path.write_bytes, data)
        return StoredImage(
            id=image_id,
            url=candidate.url,
            source=candidate.source,
            extension=ext,
            mime_type=IMAGE_MIME_TYPES[ext],
            size_bytes=len(data),
            width=width,
            height=height,
            sha256=hashlib.sha256(data).hexdigest(),
            path=path,
        )
