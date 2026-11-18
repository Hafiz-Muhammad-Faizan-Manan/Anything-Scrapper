"""Application configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General -----------------------------------------------------------------
    app_name: str = "ImageExtractor"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- HTTP fetching -------------------------------------------------------------
    # An honest, identifying User-Agent with a contact URL (the common bot convention). Many sites
    # (e.g. Wikimedia) reject clients that impersonate browsers or omit contact info.
    # Operators should replace the URL with their own contact page or email.
    user_agent: str = "ImageExtractor/1.0 (+https://example.com/imageextractor-bot)"
    # Optional override for headless Chrome; by default Chrome's own User-Agent is used.
    browser_user_agent: str | None = None
    http_connect_timeout: float = 10.0
    http_read_timeout: float = 20.0
    http_retries: int = Field(default=2, ge=0, le=5)
    http_retry_backoff: float = 0.5
    max_redirects: int = Field(default=5, ge=0, le=20)
    max_html_bytes: int = 5 * 1024 * 1024
    max_css_bytes: int = 2 * 1024 * 1024
    max_stylesheets: int = 10
    http_max_connections: int = 50

    # --- Security ------------------------------------------------------------------
    allowed_ports: str = "80,443,8080,8443"
    max_url_length: int = 2048
    # Only meant for local testing. NEVER enable in production.
    allow_private_networks: bool = False

    # --- Images --------------------------------------------------------------------
    max_image_bytes: int = 15 * 1024 * 1024
    max_job_total_bytes: int = 300 * 1024 * 1024
    max_images_per_job: int = 300
    max_concurrent_downloads: int = 8
    min_image_dimension: int = 3  # images with width AND height below this are dropped (tracking pixels)
    max_image_pixels: int = 100_000_000  # Pillow decompression-bomb guard

    # --- Selenium ------------------------------------------------------------------
    max_browser_sessions: int = Field(default=2, ge=1, le=16)
    selenium_page_load_timeout: float = 30.0
    selenium_script_timeout: float = 15.0
    selenium_max_scrolls: int = 15
    selenium_scroll_wait: float = 1.5  # max seconds to wait for new content after each scroll step
    selenium_scroll_budget: float = 15.0  # max total seconds spent scrolling
    selenium_max_bg_elements: int = 5000
    chrome_binary: str | None = None
    chromedriver_path: str | None = None
    browser_fallback_min_images: int = 3

    # --- Jobs ----------------------------------------------------------------------
    max_concurrent_jobs: int = Field(default=4, ge=1, le=64)
    job_ttl_seconds: int = 3600
    cleanup_interval_seconds: int = 120
    temp_dir: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()) / "image_extractor")

    @field_validator("chrome_binary", "chromedriver_path", "browser_user_agent", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_port_set(self) -> set[int]:
        return {int(p) for p in self.allowed_ports.split(",") if p.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
