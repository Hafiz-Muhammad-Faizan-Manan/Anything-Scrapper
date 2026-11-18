from pathlib import Path

import httpx

from app.config import Settings
from app.services.image_downloader import ImageDownloader
from app.services.image_extractor import ImageCandidate
from tests.conftest import CDN, SITE, SVG, make_image, mock_client
from app.services.http_scraper import HttpScraper

PNG = make_image("PNG", (64, 48))
JPG = make_image("JPEG", (200, 100), "blue")
PIXEL = make_image("GIF", (1, 1))


def img(body: bytes, ctype: str = "image/png") -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": ctype})


async def run(settings: Settings, routes: dict, urls: list[str], tmp_path: Path):
    http = HttpScraper(settings, mock_client(routes))
    downloader = ImageDownloader(settings, http)
    progress: list[tuple[int, int]] = []
    report = await downloader.download_all(
        [ImageCandidate(u, "img") for u in urls],
        referer=f"{SITE}/",
        job_dir=tmp_path / "job",
        on_progress=lambda p, t: progress.append((p, t)),
    )
    return report, progress


async def test_downloads_validates_and_dedupes(settings: Settings, tmp_path: Path) -> None:
    routes = {
        f"{SITE}/a.png": img(PNG),
        f"{SITE}/copy-of-a.png": img(PNG),  # same bytes, different URL
        f"{CDN}/photo.jpg": img(JPG, "application/octet-stream"),  # wrong type, right magic bytes
        f"{SITE}/logo.svg": img(SVG, "image/svg+xml"),
        f"{SITE}/fake.jpg": img(b"<html>not an image</html>", "image/jpeg"),
        f"{SITE}/page.jpg": img(b"<html></html>", "text/html"),
        f"{SITE}/pixel.gif": img(PIXEL, "image/gif"),
        f"{SITE}/broken.png": img(PNG[:40], "image/png"),
        f"{SITE}/forbidden.png": httpx.Response(403),
    }
    urls = [*routes.keys(), f"{SITE}/missing.png"]
    report, progress = await run(settings, routes, urls, tmp_path)

    names = [i.filename for i in report.images]
    assert names == ["a.png", "photo.jpg", "logo.svg"]
    by_name = {i.filename: i for i in report.images}
    assert (by_name["photo.jpg"].width, by_name["photo.jpg"].height) == (200, 100)
    assert by_name["photo.jpg"].mime_type == "image/jpeg"
    assert (by_name["logo.svg"].width, by_name["logo.svg"].height) == (120, 80)
    for i in report.images:
        assert i.path.is_file() and i.path.parent == tmp_path / "job"

    assert report.failures["duplicate"] == 1
    assert report.failures["not_image"] == 1
    assert report.failures["rejected"] == 1
    assert report.failures["tiny"] == 1
    assert report.failures["corrupt"] == 1
    assert report.failures["denied"] == 1
    assert report.failures["not_found"] == 1
    assert progress[-1] == (len(urls), len(urls))
    # Duplicate's file is removed from disk
    assert len(list((tmp_path / "job").iterdir())) == 3


async def test_size_limit_enforced(tmp_path: Path) -> None:
    settings = Settings(temp_dir=tmp_path, http_retries=0, max_image_bytes=100)
    report, _ = await run(settings, {f"{SITE}/big.jpg": img(JPG, "image/jpeg")}, [f"{SITE}/big.jpg"], tmp_path)
    assert report.images == []
    assert report.failures["too_large"] == 1


async def test_unsafe_image_urls_are_blocked(settings: Settings, tmp_path: Path) -> None:
    report, _ = await run(settings, {}, ["http://127.0.0.1/a.png", "http://10.0.0.1/b.png"], tmp_path)
    assert report.images == []
    assert report.failures["unsafe"] == 2


async def test_redirect_to_private_ip_is_blocked(settings: Settings, tmp_path: Path) -> None:
    routes = {f"{SITE}/r.png": httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})}
    report, _ = await run(settings, routes, [f"{SITE}/r.png"], tmp_path)
    assert report.failures["unsafe"] == 1


async def test_max_images_per_job(tmp_path: Path) -> None:
    settings = Settings(temp_dir=tmp_path, http_retries=0, max_images_per_job=2)
    routes = {f"{SITE}/{n}.png": img(make_image("PNG", (10 + n, 10))) for n in range(5)}
    report, _ = await run(settings, routes, list(routes), tmp_path)
    assert len(report.images) == 2
    assert report.limit_reached
