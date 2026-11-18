from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.services.http_scraper import HttpScraper

# Public literal IPs are used as hosts so tests never need DNS.
SITE = "http://93.184.215.14"
CDN = "http://151.101.1.1"


def make_image(fmt: str = "PNG", size: tuple[int, int] = (64, 48), color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect/></svg>'


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        temp_dir=tmp_path / "jobs",
        http_retries=0,
        max_concurrent_downloads=4,
        cleanup_interval_seconds=3600,
    )


Routes = dict[str, Callable[[httpx.Request], httpx.Response] | httpx.Response]


def mock_client(routes: Routes) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        route = routes.get(url)
        if route is None:
            return httpx.Response(404, text="not found")
        return route(request) if callable(route) else route

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture
def make_scraper(settings: Settings) -> Callable[[Routes], HttpScraper]:
    def factory(routes: Routes) -> HttpScraper:
        return HttpScraper(settings, mock_client(routes))

    return factory
