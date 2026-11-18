"""Headless Chrome rendering for JavaScript-driven pages.

Selenium is synchronous, so renders run on a dedicated bounded thread pool. An asyncio
semaphore with the same size caps the number of simultaneous browser sessions, so the
event loop never blocks and CPU/RAM usage stays predictable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    JavascriptException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from app.config import Settings

logger = logging.getLogger(__name__)

# Chrome-side SSRF guard: the browser itself must never reach internal hosts, even if a
# public page tries to load sub-resources from them. Patterns use CDP's wildcard syntax.
BLOCKED_URL_PATTERNS = [
    "*://localhost*", "*://127.*", "*://0.*", "*://10.*", "*://169.254.*", "*://192.168.*",
    *(f"*://172.{i}.*" for i in range(16, 32)),
    *(f"*://100.{i}.*" for i in range(64, 128)),
    "*://[::1]*", "*://[fc*", "*://[fd*", "*://[fe80*", "*://[::ffff:*",
    "*.local/*", "*.local:*", "*.internal/*", "*.internal:*", "*.localhost/*", "*.localhost:*",
    "file:*", "ftp:*",
    # Heavy, image-irrelevant resources (saves bandwidth/CPU)
    "*.mp4*", "*.webm*", "*.m3u8*", "*.mp3*", "*.woff*", "*.ttf*", "*.otf*",
]

COLLECT_JS = """
const maxBg = arguments[0];
const urls = new Set();
const abs = (u) => { try { return new URL(u, document.baseURI).href; } catch (e) { return null; } };
// Every srcset candidate is a size variant of an image the HTML parser already picks the best
// of. Remember them so the browser-chosen (often smaller) variant is not reported again.
const variants = new Set();
document.querySelectorAll('img, source').forEach(el => {
  for (const a of ['srcset', 'data-srcset', 'data-lazy-srcset']) {
    const v = el.getAttribute(a);
    if (v) v.split(/,\\s+/).forEach(part => { const u = abs(part.trim().split(/\\s+/)[0]); if (u) variants.add(u); });
  }
  if (el.tagName === 'IMG' && (el.closest('picture') || el.getAttribute('srcset'))) {
    if (el.currentSrc) variants.add(el.currentSrc);
    if (el.src) variants.add(el.src);
  }
});
const add = (u) => {
  if (!u || typeof u !== 'string' || u.startsWith('data:') || u.startsWith('blob:')) return;
  const a = abs(u); if (a && !variants.has(a)) urls.add(a);
};
document.querySelectorAll('img').forEach(img => { if (!variants.has(img.currentSrc)) add(img.currentSrc || img.src); });
document.querySelectorAll('video[poster]').forEach(v => add(v.poster));
document.querySelectorAll('svg image').forEach(i => add(i.href && i.href.baseVal));
const re = /url\\(\\s*(['"]?)(.*?)\\1\\s*\\)/g;
const els = document.querySelectorAll('body *');
const limit = Math.min(els.length, maxBg);
for (let i = 0; i < limit; i++) {
  const st = getComputedStyle(els[i]);
  for (const v of [st.backgroundImage, st.listStyleImage, st.borderImageSource]) {
    if (v && v !== 'none') { let m; re.lastIndex = 0; while ((m = re.exec(v)) !== null) add(m[2]); }
  }
  for (const pseudo of ['::before', '::after']) {
    const ps = getComputedStyle(els[i], pseudo).backgroundImage;
    if (ps && ps !== 'none') { let m; re.lastIndex = 0; while ((m = re.exec(ps)) !== null) add(m[2]); }
  }
}
try {
  performance.getEntriesByType('resource').forEach(e => {
    if (['img', 'css', 'other', 'fetch', 'xmlhttprequest', 'image'].includes(e.initiatorType)) {
      if (/\\.(jpe?g|png|gif|webp|avif|svg|bmp|ico)(\\?|#|$)/i.test(e.name) || e.initiatorType === 'img') add(e.name);
    }
  });
} catch (e) {}
return Array.from(urls);
"""

SCROLL_STATE_JS = (
    "return [document.documentElement.scrollHeight || document.body.scrollHeight, "
    "document.images.length, window.scrollY + window.innerHeight, window.scrollY];"
)

CHALLENGE_MARKERS = (
    "just a moment...",
    "attention required! | cloudflare",
    "verify you are human",
    "are you a robot",
    "captcha",
    "access denied",
)


class BrowserError(Exception):
    """Raised when rendering fails. ``message`` is safe to show to users."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class RenderResult:
    html: str
    final_url: str
    title: str | None
    extra_urls: list[str] = field(default_factory=list)
    scrolls: int = 0
    elapsed: float = 0.0
    challenge_detected: bool = False


class SeleniumScraper:
    """Owns the bounded browser pool. Create once per application."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_browser_sessions)
        self._executor = ThreadPoolExecutor(
            max_workers=settings.max_browser_sessions, thread_name_prefix="selenium"
        )
        self._service_path = settings.chromedriver_path

    async def render(self, url: str) -> RenderResult:
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self._render_sync, url)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ sync internals

    def _build_options(self) -> Options:
        opts = Options()
        for arg in (
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1366,900",
            "--disable-extensions",
            "--disable-notifications",
            "--disable-popup-blocking",
            "--mute-audio",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--blink-settings=imagesEnabled=true",
            "--hide-scrollbars",
        ):
            opts.add_argument(arg)
        if self.settings.browser_user_agent:
            opts.add_argument(f"--user-agent={self.settings.browser_user_agent}")
        opts.page_load_strategy = "eager"
        opts.add_experimental_option(
            "prefs",
            {
                "profile.default_content_setting_values.notifications": 2,
                "profile.default_content_setting_values.geolocation": 2,
                "download_restrictions": 3,  # block all downloads
            },
        )
        if self.settings.chrome_binary:
            opts.binary_location = self.settings.chrome_binary
        return opts

    def _create_driver(self) -> webdriver.Chrome:
        service = Service(executable_path=self._service_path) if self._service_path else Service()
        try:
            driver = webdriver.Chrome(options=self._build_options(), service=service)
        except WebDriverException as exc:
            logger.exception("Failed to start headless Chrome")
            raise BrowserError("The headless browser could not be started. Is Google Chrome installed?") from exc
        driver.set_page_load_timeout(self.settings.selenium_page_load_timeout)
        driver.set_script_timeout(self.settings.selenium_script_timeout)
        if not self.settings.allow_private_networks:
            try:
                driver.execute_cdp_cmd("Network.enable", {})
                driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": BLOCKED_URL_PATTERNS})
            except WebDriverException:
                logger.warning("Could not apply CDP URL blocking; continuing with navigation checks only")
        return driver

    def _render_sync(self, url: str) -> RenderResult:
        started = time.monotonic()
        driver: webdriver.Chrome | None = None
        try:
            driver = self._create_driver()
            try:
                driver.get(url)
            except TimeoutException:
                # Eager strategy + timeout: the DOM is usually usable; stop loading and continue.
                logger.info("Page load timed out for %s; continuing with partial DOM", url)
                try:
                    driver.execute_script("window.stop();")
                except WebDriverException:
                    pass

            self._wait_ready(driver)
            scrolls = self._scroll_page(driver)

            try:
                extra = driver.execute_script(COLLECT_JS, self.settings.selenium_max_bg_elements) or []
            except (JavascriptException, TimeoutException) as exc:
                logger.warning("Image collection script failed on %s: %s", url, exc)
                extra = []

            html = driver.page_source
            final_url = driver.current_url
            title = driver.title or None
            lowered_title = (title or "").lower()
            challenge = any(m in lowered_title for m in CHALLENGE_MARKERS) or (
                len(html) < 30_000 and "cf-chl" in html
            )
            return RenderResult(
                html=html,
                final_url=final_url,
                title=title,
                extra_urls=[u for u in extra if isinstance(u, str)],
                scrolls=scrolls,
                elapsed=time.monotonic() - started,
                challenge_detected=challenge,
            )
        except BrowserError:
            raise
        except InvalidSessionIdException as exc:
            logger.exception("Browser session crashed while rendering %s", url)
            raise BrowserError("The headless browser crashed while rendering the page.") from exc
        except WebDriverException as exc:
            logger.exception("WebDriver error while rendering %s", url)
            msg = (exc.msg or "").split("\n", 1)[0] if hasattr(exc, "msg") else ""
            if "ERR_NAME_NOT_RESOLVED" in msg or "ERR_CONNECTION" in msg:
                raise BrowserError("The headless browser could not reach the website.") from exc
            if "ERR_BLOCKED_BY_CLIENT" in msg:
                raise BrowserError("The page tried to load a blocked (internal) address.") from exc
            raise BrowserError("The headless browser failed to render the page.") from exc
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:  # noqa: BLE001 - best effort cleanup
                    logger.warning("driver.quit() failed", exc_info=True)

    def _wait_ready(self, driver: webdriver.Chrome) -> None:
        try:
            WebDriverWait(driver, self.settings.selenium_page_load_timeout, poll_frequency=0.2).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
        except TimeoutException:
            logger.info("document.readyState never became interactive")
        # Give the page a brief, bounded chance to reach 'complete' (images/XHR) without a fixed sleep.
        try:
            WebDriverWait(driver, min(5.0, self.settings.selenium_page_load_timeout), poll_frequency=0.25).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            pass

    def _scroll_page(self, driver: webdriver.Chrome) -> int:
        """Scroll one viewport at a time to trigger lazy loading.

        After each step an explicit wait polls until the document grows or new images appear
        (bounded by ``selenium_scroll_wait``). Scrolling stops at the bottom of the page, when
        the window cannot scroll any further, after ``selenium_max_scrolls`` steps, or when the
        overall ``selenium_scroll_budget`` is spent.
        """
        steps = 0
        deadline = time.monotonic() + self.settings.selenium_scroll_budget
        try:
            viewport = driver.execute_script("return window.innerHeight") or 900
            for _ in range(self.settings.selenium_max_scrolls):
                if time.monotonic() >= deadline:
                    logger.info("Scroll time budget exhausted after %d steps", steps)
                    break
                height, imgs, _, y_before = driver.execute_script(SCROLL_STATE_JS)
                driver.execute_script("window.scrollBy(0, arguments[0]);", int(viewport * 0.9))
                steps += 1

                def changed(d: webdriver.Chrome, h: int = height, n: int = imgs) -> bool:
                    new_h, new_n, _, _ = d.execute_script(SCROLL_STATE_JS)
                    return new_h != h or new_n != n

                wait = min(self.settings.selenium_scroll_wait, max(0.0, deadline - time.monotonic()))
                try:
                    WebDriverWait(driver, wait, poll_frequency=0.2).until(changed)
                except TimeoutException:
                    new_height, _, bottom, y_after = driver.execute_script(SCROLL_STATE_JS)
                    at_bottom = bottom >= new_height - 2
                    stuck = abs(y_after - y_before) < 1
                    if at_bottom or stuck:
                        break
            driver.execute_script("window.scrollTo(0, 0);")
        except (JavascriptException, TimeoutException) as exc:
            logger.info("Scrolling stopped early: %s", exc)
        return steps
