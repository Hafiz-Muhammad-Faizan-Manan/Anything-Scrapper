"""Background scraping jobs: storage abstraction, pipeline orchestration and cleanup.

``JobStore`` is a small protocol so the in-memory implementation used for local development
can be swapped for Redis (or another persistent store / task queue) without touching routes
or services.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.schemas.scrape_schema import JobStatus, RenderMode
from app.services.http_scraper import FetchError, HttpScraper
from app.services.image_downloader import ImageDownloader, StoredImage
from app.services.image_extractor import (
    ImageCandidate,
    candidates_from_urls,
    dedupe,
    extract_from_css,
    extract_from_html,
    needs_browser,
)
from app.services.selenium_scraper import BrowserError, SeleniumScraper
from app.utils.helpers import ID_RE, new_id
from app.utils.validator import UnsafeURLError, validate_url

logger = logging.getLogger(__name__)

HTML_TYPES = {"text/html", "application/xhtml+xml", "application/xml", "text/xml", "text/plain", ""}
BROWSER_RETRY_KINDS = {"denied", "http_error", "timeout"}

FAILURE_LABELS = {
    "not_image": "not images",
    "rejected": "not images",
    "corrupt": "corrupt",
    "tiny": "tracking pixels/tiny",
    "duplicate": "duplicates",
    "too_large": "too large",
    "unreachable": "unreachable",
    "timeout": "timed out",
    "denied": "access denied",
    "not_found": "not found (404)",
    "http_error": "HTTP errors",
    "unsafe": "blocked by security policy",
    "empty": "empty responses",
    "limit": "over the per-job limit",
    "error": "unexpected errors",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Job:
    id: str
    url: str
    render_mode: RenderMode
    directory: Path
    status: JobStatus = JobStatus.QUEUED
    message: str = "Waiting to start"
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    found: int = 0
    processed: int = 0
    total: int = 0
    used_browser: bool = False
    final_url: str | None = None
    page_title: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None
    images: list[StoredImage] = field(default_factory=list)

    def set(self, status: JobStatus | None = None, message: str | None = None) -> None:
        if status is not None:
            self.status = status
        if message is not None:
            self.message = message
        self.updated_at = utcnow()

    @property
    def total_bytes(self) -> int:
        return sum(i.size_bytes for i in self.images)

    @property
    def percent(self) -> float:
        base = {
            JobStatus.QUEUED: 0,
            JobStatus.FETCHING: 10,
            JobStatus.RENDERING: 25,
            JobStatus.EXTRACTING: 40,
            JobStatus.DOWNLOADING: 45,
            JobStatus.COMPLETED: 100,
            JobStatus.FAILED: 100,
        }[self.status]
        if self.status is JobStatus.DOWNLOADING and self.total:
            return round(45 + 55 * self.processed / self.total, 1)
        return float(base)


class JobStore(Protocol):
    async def add(self, job: Job) -> None: ...
    async def get(self, job_id: str) -> Job | None: ...
    async def save(self, job: Job) -> None: ...
    async def index_images(self, job: Job) -> None: ...
    async def get_image(self, image_id: str) -> tuple[Job, StoredImage] | None: ...
    async def delete(self, job_id: str) -> Job | None: ...
    async def expired(self, older_than: datetime) -> list[Job]: ...
    async def all(self) -> list[Job]: ...


class InMemoryJobStore:
    """Process-local job storage. Suitable for a single worker during local development."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._images: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def add(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def save(self, job: Job) -> None:
        # Objects are mutated in place; a persistent store would serialize here.
        return None

    async def index_images(self, job: Job) -> None:
        async with self._lock:
            for img in job.images:
                self._images[img.id] = job.id

    async def get_image(self, image_id: str) -> tuple[Job, StoredImage] | None:
        job_id = self._images.get(image_id)
        job = self._jobs.get(job_id) if job_id else None
        if job is None:
            return None
        for img in job.images:
            if img.id == image_id:
                return job, img
        return None

    async def delete(self, job_id: str) -> Job | None:
        async with self._lock:
            job = self._jobs.pop(job_id, None)
            if job:
                for img in job.images:
                    self._images.pop(img.id, None)
            return job

    async def expired(self, older_than: datetime) -> list[Job]:
        return [
            j for j in self._jobs.values()
            if j.status.is_terminal and (j.finished_at or j.updated_at) < older_than
        ]

    async def all(self) -> list[Job]:
        return list(self._jobs.values())


def summarize_failures(failures: Counter[str]) -> str | None:
    merged: Counter[str] = Counter()
    for kind, count in failures.items():
        merged[FAILURE_LABELS.get(kind, kind)] += count
    total = sum(merged.values())
    if not total:
        return None
    parts = ", ".join(f"{n} {label}" for label, n in merged.most_common())
    return f"{total} candidate URL(s) were skipped: {parts}."


class JobManager:
    def __init__(
        self,
        settings: Settings,
        store: JobStore,
        http: HttpScraper,
        browser: SeleniumScraper,
        downloader: ImageDownloader,
    ) -> None:
        self.settings = settings
        self.store = store
        self.http = http
        self.browser = browser
        self.downloader = downloader
        self._job_slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: set[asyncio.Task[None]] = set()
        self._sweeper: asyncio.Task[None] | None = None
        self.root = Path(settings.temp_dir)

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._remove_stale_dirs)
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="job-sweeper")

    async def stop(self) -> None:
        if self._sweeper:
            self._sweeper.cancel()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for job in await self.store.all():
            await self._delete_job(job.id)

    def _remove_stale_dirs(self) -> None:
        """Remove job directories left behind by a previous process (only our own id-named dirs)."""
        for child in self.root.iterdir():
            if child.is_dir() and ID_RE.match(child.name):
                shutil.rmtree(child, ignore_errors=True)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                await self.cleanup_expired()
            except Exception:  # noqa: BLE001
                logger.exception("Job cleanup failed")

    async def cleanup_expired(self) -> int:
        cutoff = datetime.fromtimestamp(utcnow().timestamp() - self.settings.job_ttl_seconds, UTC)
        expired = await self.store.expired(cutoff)
        for job in expired:
            await self._delete_job(job.id)
        if expired:
            logger.info("Cleaned up %d expired job(s)", len(expired))
        return len(expired)

    async def _delete_job(self, job_id: str) -> None:
        job = await self.store.delete(job_id)
        if job:
            await asyncio.to_thread(shutil.rmtree, job.directory, True)

    # ------------------------------------------------------------------ jobs

    async def submit(self, url: str, render_mode: RenderMode) -> Job:
        job_id = new_id()
        job = Job(id=job_id, url=url, render_mode=render_mode, directory=self.root / job_id)
        await self.store.add(job)
        task = asyncio.create_task(self._run(job), name=f"job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def get(self, job_id: str) -> Job | None:
        return await self.store.get(job_id)

    async def _update(self, job: Job, status: JobStatus | None = None, message: str | None = None) -> None:
        job.set(status, message)
        await self.store.save(job)

    async def _run(self, job: Job) -> None:
        async with self._job_slots:
            try:
                await self._pipeline(job)
            except asyncio.CancelledError:
                job.error = "Job was cancelled."
                job.finished_at = utcnow()
                job.set(JobStatus.FAILED, "Cancelled")
                raise
            except (FetchError, BrowserError) as exc:
                job.error = exc.message
                job.error_kind = getattr(exc, "kind", "browser")
                job.finished_at = utcnow()
                await self._update(job, JobStatus.FAILED, "Could not scan the website")
                logger.info("Job %s failed: %s", job.id, exc.message)
            except UnsafeURLError as exc:
                job.error = str(exc)
                job.error_kind = "unsafe"
                job.finished_at = utcnow()
                await self._update(job, JobStatus.FAILED, "URL rejected")
            except Exception:  # noqa: BLE001
                logger.exception("Job %s crashed", job.id)
                job.error = "An unexpected error occurred while scanning the website."
                job.error_kind = "error"
                job.finished_at = utcnow()
                await self._update(job, JobStatus.FAILED, "Unexpected error")
            finally:
                if job.status is JobStatus.FAILED:
                    await asyncio.to_thread(shutil.rmtree, job.directory, True)

    async def _pipeline(self, job: Job) -> None:
        settings = self.settings
        candidates: list[ImageCandidate] = []
        stylesheets: list[str] = []
        static_ok = False
        fetch_error: FetchError | None = None
        browser_reason: str | None = None
        page_url = job.url

        # ---- 1. Static fetch -------------------------------------------------------
        if job.render_mode != "browser":
            await self._update(job, JobStatus.FETCHING, "Fetching page HTML")
            try:
                page = await self.http.fetch_page(job.url)
            except FetchError as exc:
                if exc.kind in ("unsafe", "not_found", "unreachable") or job.render_mode == "static":
                    raise
                if exc.status_code == 401:
                    raise
                fetch_error = exc
                browser_reason = f"static request failed ({exc.message})"
            else:
                page_url = page.final_url
                job.final_url = page.final_url
                ctype = page.content_type
                if ctype.startswith("image/"):
                    # The URL itself is an image: treat it as a single-image page.
                    candidates = [ImageCandidate(url=page.final_url, source="direct")]
                    static_ok = True
                elif ctype not in HTML_TYPES:
                    raise FetchError(f"The URL does not point to a web page (Content-Type: {ctype}).", kind="rejected")
                else:
                    if page.truncated:
                        job.warnings.append("The page HTML was very large and was truncated before parsing.")
                    soup, extraction = extract_from_html(page.content, page.final_url)
                    job.page_title = extraction.title
                    candidates = extraction.candidates
                    stylesheets = extraction.stylesheet_urls
                    static_ok = True
                    if job.render_mode == "auto":
                        browser_reason = needs_browser(soup, candidates, settings.browser_fallback_min_images)
                job.found = len(candidates)
                await self._update(job, message=f"Found {len(candidates)} image reference(s) in static HTML")

        # ---- 2. Headless browser fallback ------------------------------------------
        is_direct_image = bool(candidates) and candidates[0].source == "direct"
        use_browser = not is_direct_image and (
            job.render_mode == "browser" or (job.render_mode == "auto" and browser_reason is not None)
        )

        if use_browser:
            reason = browser_reason or "browser mode selected"
            await self._update(job, JobStatus.RENDERING, f"Rendering with headless Chrome: {reason}")
            try:
                validated = await validate_url(page_url, settings)
                render = await self.browser.render(validated.url)
                try:
                    await validate_url(render.final_url, settings)
                except UnsafeURLError as exc:
                    raise BrowserError("The page redirected to a blocked address.") from exc
                if render.challenge_detected and not static_ok:
                    raise BrowserError(
                        "The website is protected by a bot check or CAPTCHA. Protected content is not scanned."
                    )
                if render.challenge_detected:
                    job.warnings.append("A bot-protection page was detected; using static HTML results only.")
                else:
                    _, rendered = extract_from_html(render.html, render.final_url)
                    extra = candidates_from_urls(render.extra_urls, render.final_url, "rendered")
                    candidates = dedupe([*candidates, *rendered.candidates, *extra])
                    stylesheets = list(dict.fromkeys([*stylesheets, *rendered.stylesheet_urls]))
                    job.page_title = job.page_title or rendered.title or render.title
                    job.final_url = render.final_url
                    page_url = render.final_url
                    job.used_browser = True
                    job.found = len(candidates)
                    logger.info(
                        "Rendered %s in %.1fs (%d scroll steps, %d candidates)",
                        render.final_url, render.elapsed, render.scrolls, len(candidates),
                    )
            except BrowserError as exc:
                if not static_ok:
                    if fetch_error is not None:
                        raise fetch_error from exc
                    raise
                job.warnings.append(f"Headless browser rendering failed ({exc.message}); showing static results.")
        elif fetch_error is not None:
            raise fetch_error

        # ---- 3. Linked stylesheets ------------------------------------------------
        await self._update(job, JobStatus.EXTRACTING, "Extracting images from stylesheets")
        sheets = stylesheets[: settings.max_stylesheets]
        if sheets:
            texts = await asyncio.gather(*(self.http.fetch_stylesheet(u, page_url) for u in sheets))
            css_candidates: list[ImageCandidate] = []
            for sheet_url, text in zip(sheets, texts, strict=True):
                if text:
                    css_candidates.extend(extract_from_css(text, sheet_url))
            candidates = dedupe([*candidates, *css_candidates])
        if len(stylesheets) > settings.max_stylesheets:
            job.warnings.append(f"Only the first {settings.max_stylesheets} stylesheets were scanned.")
        job.found = len(candidates)

        if not candidates:
            if fetch_error is not None:
                raise fetch_error
            job.finished_at = utcnow()
            await self._update(job, JobStatus.COMPLETED, "No images were found on this page")
            return

        # ---- 4. Download + validate -------------------------------------------------
        job.total = min(len(candidates), settings.max_images_per_job * 2)
        await self._update(job, JobStatus.DOWNLOADING, f"Downloading and validating {job.total} image(s)")

        async def on_progress(processed: int, total: int) -> None:
            job.processed = processed
            job.total = total
            job.set(message=f"Validated {processed} of {total} image URL(s)")

        report = await self.downloader.download_all(
            candidates, referer=page_url, job_dir=job.directory, on_progress=on_progress
        )
        job.images = report.images
        await self.store.index_images(job)

        summary = summarize_failures(report.failures)
        if summary:
            job.warnings.append(summary)
        if report.limit_reached:
            job.warnings.append(
                f"Limit reached: at most {settings.max_images_per_job} images per scan are kept."
            )
        if len(candidates) > settings.max_images_per_job * 2:
            job.warnings.append(f"The page referenced {len(candidates)} image URLs; only the first "
                                f"{settings.max_images_per_job * 2} were checked.")

        n = len(job.images)
        if n == 0 and fetch_error is not None:
            # The static request was refused and the browser fallback recovered nothing usable.
            raise fetch_error

        job.finished_at = utcnow()
        await self._update(
            job,
            JobStatus.COMPLETED,
            f"Extracted {n} image{'s' if n != 1 else ''}" if n else "No valid images could be downloaded",
        )
