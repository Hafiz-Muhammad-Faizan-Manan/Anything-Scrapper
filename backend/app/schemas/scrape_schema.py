"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobStatus(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    RENDERING = "rendering"
    EXTRACTING = "extracting"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED)


RenderMode = Literal["auto", "static", "browser"]


class ScrapeRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048, examples=["https://example.com"])
    render_mode: RenderMode = Field(
        default="auto",
        description="auto: static first, headless browser only when needed; static: never use the browser; "
        "browser: always render with headless Chrome.",
    )

    @field_validator("url")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if "://" not in v:
            v = f"https://{v}"
        return v


class ScrapeCreated(BaseModel):
    job_id: str
    status: JobStatus


class ImageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str = Field(description="Original image URL")
    filename: str
    mime_type: str
    extension: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    source: str = Field(description="Where on the page the image was found (img, srcset, css, og:image, ...)")
    preview_url: str
    download_url: str


class JobProgress(BaseModel):
    stage: JobStatus
    message: str
    found: int = 0
    processed: int = 0
    total: int = 0
    percent: float = 0.0


class JobResponse(BaseModel):
    job_id: str
    url: str
    final_url: str | None = None
    page_title: str | None = None
    status: JobStatus
    render_mode: RenderMode
    used_browser: bool = False
    progress: JobProgress
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    image_count: int = 0
    total_bytes: int = 0
    images: list[ImageInfo] = Field(default_factory=list)
    zip_url: str | None = None


class ErrorResponse(BaseModel):
    detail: str
