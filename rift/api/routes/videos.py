import io
from datetime import datetime, timezone, timedelta
from typing import List
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rift.core.database import get_db
from rift.core.config import settings
from rift.models import User, Upload
from rift.core.schemas import UploadOut, AudioUploadOut, Msg
from rift.api.deps import current_user, verified_user, get_storage_svc, get_video_svc
from rift.services.video import VideoService
import re

router = APIRouter(prefix="/videos", tags=["Videos"])

VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "m4v", "mpg", "mpeg"}
AUDIO_EXTS = {"mp3", "wav", "aac", "ogg", "flac", "m4a"}


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _sanitize(name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s\-\.]", "", name)
    return re.sub(r"\s+", "_", name.strip())[:255] or "upload"


@router.post("/upload", response_model=UploadOut, status_code=201)
async def upload_video(
    file: UploadFile = File(...),
    user: User = Depends(verified_user),
    db: AsyncSession = Depends(get_db),
    storage=Depends(get_storage_svc),
    video_svc: VideoService = Depends(get_video_svc),
):
    if not file.filename:
        raise HTTPException(400, detail={"error": "VALIDATION_ERROR", "message": "No filename"})
    orig = _sanitize(file.filename)
    ext = _ext(orig)
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, detail={"error": "INVALID_FILE_TYPE",
                                          "message": f"Extension .{ext} not allowed. Allowed: {VIDEO_EXTS}"})

    content = await file.read()
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, detail={"error": "FILE_TOO_LARGE",
                                          "message": f"File exceeds {settings.MAX_UPLOAD_BYTES // (1024**3)}GB limit"})

    try:
        path, stored_name, size = storage.save_upload(user.id, io.BytesIO(content), orig)
    except Exception as e:
        raise HTTPException(500, detail={"error": "STORAGE_ERROR", "message": str(e)})

    try:
        meta = video_svc.probe(path)
    except Exception as e:
        storage.delete(path)
        raise HTTPException(400, detail={"error": "VIDEO_ERROR", "message": f"Cannot read video: {e}"})

    if meta.duration > settings.MAX_VIDEO_SECONDS:
        storage.delete(path)
        raise HTTPException(400, detail={"error": "VIDEO_TOO_LONG",
                                          "message": f"Video exceeds {int(settings.MAX_VIDEO_SECONDS/60)} minute limit"})

    upload = Upload(
        user_id=user.id, filename=orig, stored_name=stored_name, storage_path=path,
        storage_backend=settings.STORAGE_BACKEND, size_bytes=size,
        width=meta.width, height=meta.height, fps=meta.fps,
        frame_count=meta.frame_count, duration=meta.duration,
        codec=meta.codec, has_audio=meta.has_audio, audio_codec=meta.audio_codec,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.UPLOAD_RETAIN_HOURS),
    )
    db.add(upload)
    user.storage_bytes = (user.storage_bytes or 0) + size
    await db.commit()
    await db.refresh(upload)
    return UploadOut(**upload.to_dict())


@router.post("/upload-audio", response_model=AudioUploadOut, status_code=201)
async def upload_audio(
    file: UploadFile = File(...),
    user: User = Depends(verified_user),
    storage=Depends(get_storage_svc),
):
    if not file.filename:
        raise HTTPException(400, detail={"error": "VALIDATION_ERROR", "message": "No filename"})
    orig = _sanitize(file.filename)
    ext = _ext(orig)
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, detail={"error": "INVALID_FILE_TYPE",
                                          "message": f"Extension .{ext} not allowed"})
    content = await file.read()
    try:
        path, _, size = storage.save_upload(user.id, io.BytesIO(content), orig)
    except Exception as e:
        raise HTTPException(500, detail={"error": "STORAGE_ERROR", "message": str(e)})
    return AudioUploadOut(path=path, filename=orig, size_bytes=size)


@router.get("", response_model=List[UploadOut])
async def list_uploads(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(
        select(Upload).where(Upload.user_id == user.id, Upload.is_active == True)
        .order_by(Upload.created_at.desc()).limit(50)
    )
    return [UploadOut(**u.to_dict()) for u in r.scalars()]


@router.delete("/{upload_id}", response_model=Msg)
async def delete_upload(upload_id: str, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db), storage=Depends(get_storage_svc)):
    r = await db.execute(select(Upload).where(Upload.id == upload_id, Upload.user_id == user.id))
    upload = r.scalar_one_or_none()
    if not upload:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "Upload not found"})
    storage.delete(upload.storage_path)
    user.storage_bytes = max(0, (user.storage_bytes or 0) - (upload.size_bytes or 0))
    upload.is_active = False
    await db.commit()
    return Msg(message="Upload deleted")