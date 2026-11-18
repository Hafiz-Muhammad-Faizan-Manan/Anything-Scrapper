import io
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.selenium_scraper import BrowserError, RenderResult
from tests.conftest import SITE, SVG, make_image, mock_client

PNG = make_image("PNG", (40, 30))
JPG = make_image("JPEG", (300, 200))

STATIC_HTML = f"""<html><head><title>Static</title><link rel="stylesheet" href="/s.css"></head><body>
<p>{"Lots of server rendered text. " * 20}</p>
<img src="/a.png"><img src="/b.jpg"><img src="/logo.svg">
</body></html>"""
SPA_HTML = '<html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
RENDERED_HTML = '<html><body><div id="root"><img src="/a.png"><img src="/b.jpg"></div></body></html>'


class FakeBrowser:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def render(self, url: str) -> RenderResult:
        self.calls.append(url)
        if self.fail:
            raise BrowserError("boom")
        return RenderResult(html=RENDERED_HTML, final_url=url, title="App", extra_urls=[f"{SITE}/logo.svg"])

    def shutdown(self) -> None:
        pass


ROUTES = {
    f"{SITE}/static": httpx.Response(200, text=STATIC_HTML, headers={"content-type": "text/html; charset=utf-8"}),
    f"{SITE}/spa": httpx.Response(200, text=SPA_HTML, headers={"content-type": "text/html"}),
    f"{SITE}/s.css": httpx.Response(200, text=".x{background:url(bg.jpg)}", headers={"content-type": "text/css"}),
    f"{SITE}/bg.jpg": httpx.Response(200, content=make_image("JPEG", (50, 50), "green")),
    f"{SITE}/a.png": httpx.Response(200, content=PNG, headers={"content-type": "image/png"}),
    f"{SITE}/b.jpg": httpx.Response(200, content=JPG, headers={"content-type": "image/jpeg"}),
    f"{SITE}/logo.svg": httpx.Response(200, content=SVG, headers={"content-type": "image/svg+xml"}),
    f"{SITE}/empty": httpx.Response(200, text="<html><body><p>No images here</p></body></html>",
                                    headers={"content-type": "text/html"}),
}


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as c:
        manager = app.state.job_manager
        manager.http.client = mock_client(ROUTES)
        manager.browser = FakeBrowser()
        c.manager = manager  # type: ignore[attr-defined]
        yield c


def wait_for(client: TestClient, job_id: str, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/scrape/{job_id}").json()
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_rejects_unsafe_url(client: TestClient) -> None:
    for url in ("http://localhost:8000", "http://169.254.169.254/", "file:///etc/passwd", "http://10.0.0.1"):
        r = client.post("/api/scrape", json={"url": url})
        assert r.status_code == 400, url
        assert r.json()["detail"]


def test_unknown_and_malformed_ids(client: TestClient) -> None:
    assert client.get("/api/scrape/0123456789abcdef").status_code == 404
    assert client.get("/api/scrape/..%2F..%2Fetc").status_code in (404, 422)
    assert client.get("/api/image/not-an-id").status_code == 422
    assert client.get("/api/download/0123456789abcdef").status_code == 404


def test_static_flow_preview_download_and_zip(client: TestClient) -> None:
    r = client.post("/api/scrape", json={"url": f"{SITE}/static"})
    assert r.status_code == 202
    data = wait_for(client, r.json()["job_id"])
    assert data["status"] == "completed", data
    assert data["used_browser"] is False
    assert client.manager.browser.calls == []
    assert data["page_title"] == "Static"
    assert [i["filename"] for i in data["images"]] == ["a.png", "b.jpg", "logo.svg", "bg.jpg"]
    b = data["images"][1]
    assert (b["width"], b["height"], b["mime_type"]) == (300, 200, "image/jpeg")

    preview = client.get(b["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.content == JPG

    svg = client.get(data["images"][2]["preview_url"])
    assert "sandbox" in svg.headers["content-security-policy"]

    dl = client.get(b["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].startswith("attachment")

    z = client.get(data["zip_url"])
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(z.content))
    assert sorted(archive.namelist()) == ["a.png", "b.jpg", "bg.jpg", "logo.svg"]
    assert archive.read("b.jpg") == JPG

    # Image ids are bound to their job
    other_job = "0123456789abcdef"
    assert client.get(f"/api/download/{other_job}/{b['id']}").status_code == 404


def test_spa_uses_browser_fallback(client: TestClient) -> None:
    r = client.post("/api/scrape", json={"url": f"{SITE}/spa"})
    data = wait_for(client, r.json()["job_id"])
    assert data["status"] == "completed", data
    assert data["used_browser"] is True
    assert client.manager.browser.calls == [f"{SITE}/spa"]
    assert {i["filename"] for i in data["images"]} == {"a.png", "b.jpg", "logo.svg"}


def test_static_mode_never_uses_browser(client: TestClient) -> None:
    r = client.post("/api/scrape", json={"url": f"{SITE}/spa", "render_mode": "static"})
    data = wait_for(client, r.json()["job_id"])
    assert data["status"] == "completed"
    assert data["image_count"] == 0
    assert client.manager.browser.calls == []


def test_browser_failure_degrades_to_static(client: TestClient) -> None:
    client.manager.browser = FakeBrowser(fail=True)
    r = client.post("/api/scrape", json={"url": f"{SITE}/empty"})
    data = wait_for(client, r.json()["job_id"])
    assert data["status"] == "completed"
    assert data["image_count"] == 0
    assert any("Headless browser rendering failed" in w for w in data["warnings"])
    assert data["zip_url"] is None


def test_unreachable_page_fails_job(client: TestClient) -> None:
    r = client.post("/api/scrape", json={"url": f"{SITE}/does-not-exist"})
    data = wait_for(client, r.json()["job_id"])
    assert data["status"] == "failed"
    assert "404" in data["error"]
    assert client.get(f"/api/download/{data['job_id']}").status_code == 409
