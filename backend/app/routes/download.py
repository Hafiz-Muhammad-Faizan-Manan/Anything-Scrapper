"""Image preview, single download and ZIP download endpoints.

Files are only ever located through the job store using server-generated ids (validated by
regex), never by joining user input into a filesystem path.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import FileResponse, StreamingResponse

from app.routes.scrape import get_manager
from app.schemas.scrape_schema import ErrorResponse, JobStatus
from app.services.image_downloader import StoredImage
from app.services.job_manager import JobManager
from app.services.zip_service import ZipEntry, stream_zip
from app.utils.helpers import ID_PATTERN

router = APIRouter(prefix="/api", tags=["download"])

IMAGE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # Neutralizes scripts inside SVGs if an image URL is opened directly on the API origin.
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
    "Cache-Control": "private, max-age=3600",
    "Cross-Origin-Resource-Policy": "cross-origin",
}


def _file_response(img: StoredImage, *, attachment: bool) -> FileResponse:
    if not img.path.is_file():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="The cached image is no longer available.")
    return FileResponse(
        img.path,
        media_type=img.mime_type,
        filename=img.filename,
        content_disposition_type="attachment" if attachment else "inline",
        headers=IMAGE_SECURITY_HEADERS,
    )


@router.get(
    "/image/{image_id}",
    responses={404: {"model": ErrorResponse}},
    summary="Image preview (served inline from the job cache)",
)
async def preview_image(
    image_id: str = Path(..., pattern=ID_PATTERN),
    manager: JobManager = Depends(get_manager),
) -> FileResponse:
    found = await manager.store.get_image(image_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found or expired.")
    _, img = found
    return _file_response(img, attachment=False)


@router.get(
    "/download/{job_id}/{image_id}",
    responses={404: {"model": ErrorResponse}},
    summary="Download a single image",
)
async def download_image(
    job_id: str = Path(..., pattern=ID_PATTERN),
    image_id: str = Path(..., pattern=ID_PATTERN),
    manager: JobManager = Depends(get_manager),
) -> FileResponse:
    found = await manager.store.get_image(image_id)
    if found is None or found[0].id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found or expired.")
    return _file_response(found[1], attachment=True)


@router.get(
    "/download/{job_id}",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Download all images of a job as a streamed ZIP archive",
)
async def download_zip(
    job_id: str = Path(..., pattern=ID_PATTERN),
    manager: JobManager = Depends(get_manager),
) -> StreamingResponse:
    job = await manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or expired.")
    if job.status is not JobStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The job has not finished yet.")
    entries = [ZipEntry(arcname=img.filename, path=img.path, extension=img.extension)
               for img in job.images if img.path.is_file()]
    if not entries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This job has no images to download.")

    host = re.sub(r"[^A-Za-z0-9.-]+", "-", urlsplit(job.final_url or job.url).hostname or "site")[:60]
    filename = f"imageextractor-{host}-{job.id}.zip"
    return StreamingResponse(
        stream_zip(entries),  # sync generator -> iterated in Starlette's threadpool
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-Image-Count": str(len(entries)),
            "X-Uncompressed-Size": str(sum(img.size_bytes for img in job.images)),
            "Cache-Control": "no-store",
        },
    )
