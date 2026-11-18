"""Scrape job endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.schemas.scrape_schema import (
    ErrorResponse,
    ImageInfo,
    JobProgress,
    JobResponse,
    JobStatus,
    ScrapeCreated,
    ScrapeRequest,
)
from app.services.job_manager import Job, JobManager
from app.utils.helpers import ID_PATTERN
from app.utils.validator import UnsafeURLError, validate_url

router = APIRouter(prefix="/api", tags=["scrape"])


def get_manager(request: Request) -> JobManager:
    return request.app.state.job_manager


def job_to_response(job: Job) -> JobResponse:
    images = [
        ImageInfo(
            id=img.id,
            url=img.url,
            filename=img.filename,
            mime_type=img.mime_type,
            extension=img.extension,
            size_bytes=img.size_bytes,
            width=img.width,
            height=img.height,
            source=img.source,
            preview_url=f"/api/image/{img.id}",
            download_url=f"/api/download/{job.id}/{img.id}",
        )
        for img in job.images
    ]
    return JobResponse(
        job_id=job.id,
        url=job.url,
        final_url=job.final_url,
        page_title=job.page_title,
        status=job.status,
        render_mode=job.render_mode,
        used_browser=job.used_browser,
        progress=JobProgress(
            stage=job.status,
            message=job.message,
            found=job.found,
            processed=job.processed,
            total=job.total,
            percent=job.percent,
        ),
        warnings=list(job.warnings),
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
        image_count=len(images),
        total_bytes=job.total_bytes,
        images=images,
        zip_url=f"/api/download/{job.id}" if job.status is JobStatus.COMPLETED and images else None,
    )


@router.post(
    "/scrape",
    response_model=ScrapeCreated,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}},
    summary="Create a scraping job for a single web page",
)
async def create_scrape_job(body: ScrapeRequest, manager: JobManager = Depends(get_manager)) -> ScrapeCreated:
    try:
        validated = await validate_url(body.url, manager.settings)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    job = await manager.submit(validated.url, body.render_mode)
    return ScrapeCreated(job_id=job.id, status=job.status)


@router.get(
    "/scrape/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get job status, progress and extracted images",
)
async def get_scrape_job(
    job_id: str = Path(..., pattern=ID_PATTERN),
    manager: JobManager = Depends(get_manager),
) -> JobResponse:
    job = await manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or expired.")
    return job_to_response(job)
