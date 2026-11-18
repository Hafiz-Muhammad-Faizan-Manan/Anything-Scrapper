"""SSRF-safe HTTP fetching built on a shared ``httpx.AsyncClient``.

Redirects are followed manually so that every hop is re-validated, bodies are streamed with
hard size limits and transient failures are retried with backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from app.config import Settings
from app.utils.helpers import retry_async
from app.utils.validator import UnsafeURLError, validate_url

logger = logging.getLogger(__name__)

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RETRYABLE_STATUSES = {429, 502, 503, 504}
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
IMAGE_ACCEPT = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
CSS_ACCEPT = "text/css,*/*;q=0.1"


class FetchError(Exception):
    """A user-presentable fetch failure."""

    def __init__(self, message: str, *, kind: str = "error", status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind  # unsafe | unreachable | timeout | denied | not_found | http_error | too_large | rejected
        self.status_code = status_code


class _RetryableStatus(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@dataclass
class _Redirect:
    location: str


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    headers: httpx.Headers
    content: bytes
    truncated: bool = False
    redirects: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def charset(self) -> str | None:
        ct = self.headers.get("content-type", "")
        for part in ct.split(";")[1:]:
            key, _, value = part.partition("=")
            if key.strip().lower() == "charset" and value.strip():
                return value.strip().strip("\"'")
        return None

    def text(self) -> str:
        encoding = self.charset or "utf-8"
        try:
            return self.content.decode(encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


HeaderCheck = Callable[[httpx.Headers], None]


def _should_retry(exc: BaseException) -> bool:
    return isinstance(exc, (_RetryableStatus, httpx.TransportError)) and not isinstance(
        exc, httpx.UnsupportedProtocol
    )


def build_client(settings: Settings) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_read_timeout,
        pool=settings.http_connect_timeout,
    )
    limits = httpx.Limits(
        max_connections=settings.http_max_connections,
        max_keepalive_connections=max(10, settings.http_max_connections // 2),
    )
    return httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,  # redirects are followed manually and re-validated
        headers={"User-Agent": settings.user_agent, "Accept-Language": "en-US,en;q=0.9"},
        http2=False,
        trust_env=False,  # never route through env-configured proxies implicitly
    )


class HttpScraper:
    """Safe HTTP GET with manual redirect validation, retries and body size limits."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        accept: str = "*/*",
        referer: str | None = None,
        header_check: HeaderCheck | None = None,
        truncate: bool = False,
    ) -> FetchResult:
        """GET ``url`` safely.

        :param max_bytes: hard limit on the body size.
        :param header_check: called with the final response headers before the body is read;
            raise :class:`FetchError` from it to reject a response early.
        :param truncate: when True, stop reading at ``max_bytes`` instead of failing.
        """
        current = url
        redirects: list[str] = []
        for _ in range(self.settings.max_redirects + 1):
            try:
                validated = await validate_url(current, self.settings)
            except UnsafeURLError as exc:
                raise FetchError(str(exc), kind="unsafe") from exc

            target = validated.url
            headers = {"Accept": accept}
            if referer:
                headers["Referer"] = referer

            try:
                outcome = await retry_async(
                    lambda: self._request_once(target, headers, max_bytes, header_check, truncate),
                    retries=self.settings.http_retries,
                    backoff=self.settings.http_retry_backoff,
                    should_retry=_should_retry,
                    description=f"GET {target}",
                )
            except _RetryableStatus as exc:
                if exc.status_code == 429:
                    raise FetchError(
                        "The website is rate limiting requests (HTTP 429). Try again later.",
                        kind="denied",
                        status_code=429,
                    ) from exc
                raise FetchError(
                    f"The website is temporarily unavailable (HTTP {exc.status_code}).",
                    kind="http_error",
                    status_code=exc.status_code,
                ) from exc
            except httpx.TimeoutException as exc:
                raise FetchError("The request timed out.", kind="timeout") from exc
            except httpx.ConnectError as exc:
                raise FetchError("Could not connect to the website.", kind="unreachable") from exc
            except httpx.HTTPError as exc:
                raise FetchError(f"Network error: {exc.__class__.__name__}.", kind="unreachable") from exc

            if isinstance(outcome, _Redirect):
                redirects.append(target)
                current = urljoin(target, outcome.location)
                continue
            outcome.requested_url = url
            outcome.redirects = redirects
            return outcome

        raise FetchError("Too many redirects.", kind="http_error")

    async def _request_once(
        self,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
        header_check: HeaderCheck | None,
        truncate: bool,
    ) -> FetchResult | _Redirect:
        async with self.client.stream("GET", url, headers=headers) as resp:
            status = resp.status_code
            if status in REDIRECT_STATUSES:
                location = resp.headers.get("location")
                if not location:
                    raise FetchError(f"Redirect (HTTP {status}) without a Location header.", kind="http_error")
                return _Redirect(location=location)
            if status in RETRYABLE_STATUSES:
                raise _RetryableStatus(status)
            if status in (401, 403, 407, 451):
                raise FetchError(
                    f"Access denied (HTTP {status}). Content behind logins, paywalls or bot protection "
                    "cannot be scanned.",
                    kind="denied",
                    status_code=status,
                )
            if status == 404 or status == 410:
                raise FetchError(f"Not found (HTTP {status}).", kind="not_found", status_code=status)
            if status >= 400:
                raise FetchError(f"The server responded with HTTP {status}.", kind="http_error", status_code=status)

            if header_check is not None:
                header_check(resp.headers)

            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes and not truncate:
                raise FetchError("The response exceeds the maximum allowed size.", kind="too_large")

            buf = bytearray()
            truncated = False
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    if truncate:
                        del buf[max_bytes:]
                        truncated = True
                        break
                    raise FetchError("The response exceeds the maximum allowed size.", kind="too_large")

            return FetchResult(
                requested_url=url,
                final_url=str(resp.url),
                status_code=status,
                headers=resp.headers,
                content=bytes(buf),
                truncated=truncated,
            )

    async def fetch_page(self, url: str) -> FetchResult:
        return await self.fetch(url, max_bytes=self.settings.max_html_bytes, accept=HTML_ACCEPT, truncate=True)

    async def fetch_stylesheet(self, url: str, referer: str) -> str | None:
        """Fetch a linked stylesheet; failures are non-fatal and return None."""

        def check(headers: httpx.Headers) -> None:
            ct = headers.get("content-type", "").lower()
            if ct and "css" not in ct and "text/plain" not in ct:
                raise FetchError("Not a stylesheet.", kind="rejected")

        try:
            result = await self.fetch(
                url, max_bytes=self.settings.max_css_bytes, accept=CSS_ACCEPT, referer=referer,
                header_check=check, truncate=True,
            )
        except FetchError as exc:
            logger.debug("Skipping stylesheet %s: %s", url, exc.message)
            return None
        return result.text()
