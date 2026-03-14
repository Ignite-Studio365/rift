import math
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from rift.core.database import get_db
from rift.core.config import settings
from rift.models import User, Upload, Job, JobStatus
from rift.core.schemas import (
    CreateJobIn, CreateJobOut, JobOut, JobListOut,
    PreviewIn, PreviewOut, ThumbnailIn, ThumbnailOut, Msg,
)
from rift.api.deps import (
    current_user, verified_user, quota_user, get_storage_svc,
    get_video_svc, get_billing, pagination,
)
from rift.services.billing import BillingService
from rift.services.video import VideoService

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post("", response_model=CreateJobOut, status_code=202)
async def create_job(
    body: CreateJobIn,
    user: User = Depends(quota_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(Upload).where(
        Upload.id == body.upload_id, Upload.user_id == user.id, Upload.is_active == True))
    upload = r.scalar_one_or_none()
    if not upload:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Upload not found"})

    params = body.params.to_dict()

    from rift.models import Plan
    cost = settings.PRICE_PER_VIDEO_CENTS if user.plan == Plan.pay_per_video else 0
    if user.plan == Plan.pay_per_video:
        user.credits = max(0, (user.credits or 0) - 1)

    import uuid
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id, user_id=user.id, upload_id=upload.id,
        status=JobStatus.queued, progress=0.0,
        message="Queued for processing", params=params,
        queued_at=datetime.now(timezone.utc), cost_cents=cost,
    )
    db.add(job)
    await db.commit()

    from rift.worker.tasks import render
    task = render.apply_async(
        kwargs={"job_id": job_id, "user_id": user.id,
                "source_path": upload.storage_path, "params": params},
        queue="render",
    )
    job.celery_id = task.id
    await db.commit()

    return CreateJobOut(job_id=job_id, celery_id=task.id)


@router.get("", response_model=JobListOut)
async def list_jobs(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    pg: dict = Depends(pagination),
):
    q = select(Job).where(Job.user_id == user.id)
    if status and status in [s.value for s in JobStatus]:
        q = q.where(Job.status == JobStatus(status))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    jobs = (await db.execute(q.order_by(Job.created_at.desc()).offset(pg["offset"]).limit(pg["per_page"]))).scalars().all()
    return JobListOut(
        jobs=[JobOut(**j.to_dict()) for j in jobs],
        total=total, page=pg["page"], per_page=pg["per_page"],
        pages=math.ceil(total / pg["per_page"]) if pg["per_page"] else 0,
    )


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = r.scalar_one_or_none()
    if not job:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Job not found"})
    return JobOut(**job.to_dict())


@router.post("/{job_id}/cancel", response_model=Msg)
async def cancel_job(job_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = r.scalar_one_or_none()
    if not job:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Job not found"})
    if job.status not in (JobStatus.pending, JobStatus.queued, JobStatus.processing):
        raise HTTPException(400, detail={"error": "INVALID_STATE", "message": f"Cannot cancel {job.status.value} job"})
    if job.celery_id:
        from rift.worker.celery import celery_app
        celery_app.control.revoke(job.celery_id, terminate=True, signal="SIGTERM")
    job.status = JobStatus.cancelled
    job.message = "Cancelled by user"
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return Msg(message="Job cancelled")


@router.delete("/{job_id}", response_model=Msg)
async def delete_job(job_id: str, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db), storage=Depends(get_storage_svc)):
    r = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = r.scalar_one_or_none()
    if not job:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Job not found"})
    if job.out_path:
        storage.delete(job.out_path)
    await db.delete(job)
    await db.commit()
    return Msg(message="Job deleted")


@router.get("/{job_id}/download")
async def download(job_id: str, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_db), storage=Depends(get_storage_svc)):
    r = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = r.scalar_one_or_none()
    if not job:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Job not found"})
    if job.status != JobStatus.complete:
        raise HTTPException(400, detail={"error": "NOT_READY", "message": "Job not complete"})
    if job.out_expires_at and job.out_expires_at < datetime.now(timezone.utc):
        raise HTTPException(410, detail={"error": "EXPIRED", "message": "Download expired"})
    if not job.out_path:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "No output file"})

    if settings.STORAGE_BACKEND == "r2":
        url = storage.presigned_url(job.out_path, filename=job.out_filename, ttl=3600)
        job.download_count = (job.download_count or 0) + 1
        await db.commit()
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=url)

    fpath = storage.get_path(job.out_path)
    if not fpath or not fpath.exists():
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "File missing"})
    job.download_count = (job.download_count or 0) + 1
    await db.commit()
    ext = (job.out_filename or "output.mp4").rsplit(".", 1)[-1].lower()
    mime = {"mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
            "mkv": "video/x-matroska", "webm": "video/webm"}.get(ext, "video/mp4")
    return StreamingResponse(storage.stream(job.out_path), media_type=mime,
                             headers={"Content-Disposition": f'attachment; filename="{job.out_filename or "output." + ext}"'})


@router.get("/{job_id}/stream")
async def stream(job_id: str, request: Request, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db), storage=Depends(get_storage_svc)):
    r = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    job = r.scalar_one_or_none()
    if not job or job.status != JobStatus.complete or not job.out_path:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Job output not found"})
    fpath = storage.get_path(job.out_path)
    if not fpath or not fpath.exists():
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "File missing"})
    size = fpath.stat().st_size
    rng = request.headers.get("Range")
    if not rng:
        return StreamingResponse(storage.stream(job.out_path), media_type="video/mp4",
                                 headers={"Content-Length": str(size), "Accept-Ranges": "bytes"})
    m = re.search(r"bytes=(\d+)-(\d*)", rng)
    if not m:
        raise HTTPException(416)
    b1 = int(m.group(1))
    b2 = int(m.group(2)) if m.group(2) else size - 1
    b2 = min(b2, size - 1)
    length = b2 - b1 + 1
    def _gen():
        with open(fpath, "rb") as f:
            f.seek(b1)
            rem = length
            while rem > 0:
                chunk = f.read(min(1024 * 1024, rem))
                if not chunk: break
                yield chunk
                rem -= len(chunk)
    return StreamingResponse(_gen(), status_code=206, media_type="video/mp4",
                             headers={"Content-Range": f"bytes {b1}-{b2}/{size}",
                                      "Accept-Ranges": "bytes", "Content-Length": str(length)})


@router.post("/preview", response_model=PreviewOut)
async def gen_preview(body: PreviewIn, user: User = Depends(verified_user),
                      db: AsyncSession = Depends(get_db), storage=Depends(get_storage_svc)):
    r = await db.execute(select(Upload).where(
        Upload.id == body.upload_id, Upload.user_id == user.id, Upload.is_active == True))
    upload = r.scalar_one_or_none()
    if not upload:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Upload not found"})
    fpath = storage.get_path(upload.storage_path)
    if not fpath:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Video file missing"})
    import asyncio
    from rift.worker.tasks import preview as preview_task
    task = preview_task.apply_async(
        kwargs={"user_id": user.id, "video_path": str(fpath),
                "frame_index": body.frame_index, "params": body.params.to_dict()},
        queue="preview",
    )
    # task.get() is blocking — run in a thread executor so the async event loop
    # is not blocked while waiting. Other requests continue to be served normally.
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: task.get(timeout=settings.PREVIEW_TIMEOUT)
    )
    if not result or not result.get("success"):
        raise HTTPException(500, detail={"error": "PREVIEW_FAILED",
                                          "message": (result or {}).get("error", "Preview failed")})
    return PreviewOut(image=result["image"], width=result["width"],
                      height=result["height"], frame_index=result["frame_index"])


@router.post("/thumbnail", response_model=ThumbnailOut)
async def thumbnail(body: ThumbnailIn, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db), storage=Depends(get_storage_svc),
                    video_svc: VideoService = Depends(get_video_svc)):
    r = await db.execute(select(Upload).where(
        Upload.id == body.upload_id, Upload.user_id == user.id, Upload.is_active == True))
    upload = r.scalar_one_or_none()
    if not upload:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Upload not found"})
    fpath = storage.get_path(upload.storage_path)
    if not fpath:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "File missing"})
    try:
        frame, idx = video_svc.frame_at_time(str(fpath), body.time_seconds, max_w=body.max_width)
    except Exception as e:
        raise HTTPException(500, detail={"error": "VIDEO_ERROR", "message": str(e)})
    import base64, cv2
    from io import BytesIO
    from PIL import Image
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    buf = BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=85)
    return ThumbnailOut(image=base64.b64encode(buf.getvalue()).decode(),
                        width=frame.shape[1], height=frame.shape[0],
                        time_seconds=body.time_seconds)