"""FastAPI application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.routes import download, scrape
from app.services.http_scraper import HttpScraper, build_client
from app.services.image_downloader import ImageDownloader
from app.services.job_manager import InMemoryJobStore, JobManager
from app.services.selenium_scraper import SeleniumScraper

logger = logging.getLogger("app")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Selenium/urllib3 are chatty at INFO.
    for noisy in ("selenium", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = build_client(settings)
        http = HttpScraper(settings, client)
        browser = SeleniumScraper(settings)
        manager = JobManager(
            settings=settings,
            store=InMemoryJobStore(),
            http=http,
            browser=browser,
            downloader=ImageDownloader(settings, http),
        )
        await manager.start()
        app.state.settings = settings
        app.state.job_manager = manager
        logger.info("%s started (temp dir: %s)", settings.app_name, settings.temp_dir)
        try:
            yield
        finally:
            await manager.stop()
            browser.shutdown()
            await client.aclose()
            logger.info("%s stopped", settings.app_name)

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        description="Extract, preview and download images from a single public web page.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition", "Content-Length", "X-Image-Count", "X-Uncompressed-Size"],
    )
    app.include_router(scrape.router)
    app.include_router(download.router)

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    return app


app = create_app()
