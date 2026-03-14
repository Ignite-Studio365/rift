import asyncio
import gc
import os
import shutil
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import cv2
import numpy as np
import torch
from celery import Task
from celery.utils.log import get_task_logger

from rift.worker.celery import celery_app
from rift.core.config import settings

log = get_task_logger(__name__)

# Module-level singletons — loaded once per worker process
_effects = None
_overlays = None
_upscaler = None
_video_svc = None
_audio_svc = None


def _get_effects():
    global _effects
    if _effects is None:
        from rift.services.effects import Effects
        _effects = Effects()
    return _effects


def _get_overlays():
    global _overlays
    if _overlays is None:
        from rift.services.overlays import Overlays
        _overlays = Overlays()
    return _overlays


def _get_upscaler():
    global _upscaler
    if _upscaler is None:
        from rift.services.upscaler import UpscalerService
        _upscaler = UpscalerService()
    return _upscaler


def _get_video():
    global _video_svc
    if _video_svc is None:
        from rift.services.video import VideoService
        _video_svc = VideoService()
    return _video_svc


def _get_audio():
    global _audio_svc
    if _audio_svc is None:
        from rift.services.audio import AudioService
        _audio_svc = AudioService()
    return _audio_svc


def _upscale_dims(src_w: int, src_h: int, target: str):
    if target == "Original":
        return src_w, src_h, "none", 1.0
    MAP = {
        "2K Lanczos": (2.0, "lanczos"), "4K Lanczos": (4.0, "lanczos"), "8K Lanczos": (8.0, "lanczos"),
        "2K AI Enhanced": (2.0, "ai"),   "4K AI Enhanced": (4.0, "ai"),   "8K AI Enhanced": (8.0, "ai"),
    }
    if target not in MAP:
        return src_w, src_h, "none", 1.0
    scale, method = MAP[target]
    w = int(src_w * scale)
    h = int(src_h * scale)
    w = w if w % 2 == 0 else w - 1
    h = h if h % 2 == 0 else h - 1
    return max(2, w), max(2, h), method, scale


def _update_job(job_id: str, **kwargs):
    async def _do():
        from rift.core.database import get_db
        from rift.models import Job, JobStatus, User
        from sqlalchemy import select
        from rift.core.database import session_factory
        async with session_factory()() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            status = kwargs.get("status")
            if status:
                try: job.status = JobStatus(status)
                except ValueError: pass
            for f in ("progress", "message", "error", "frames_done", "frames_total",
                       "render_fps", "out_path", "out_filename", "out_size_bytes",
                       "out_width", "out_height", "out_fps", "out_format", "out_expires_at"):
                if f in kwargs:
                    setattr(job, f, kwargs[f])
            if status == "processing" and job.started_at is None:
                job.started_at = datetime.now(timezone.utc)
            if status in ("complete", "failed", "cancelled"):
                job.completed_at = datetime.now(timezone.utc)
            if status == "complete":
                user = await session.get(User, job.user_id)
                if user:
                    user.total_renders = (user.total_renders or 0) + 1
                    user.renders_this_month = (user.renders_this_month or 0) + 1
                    if "out_expires_at" not in kwargs:
                        job.out_expires_at = datetime.now(timezone.utc) + timedelta(days=settings.OUTPUT_RETAIN_DAYS)
            await session.commit()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do())
    except Exception as e:
        log.error(f"DB update failed for job {job_id}: {e}")
    finally:
        loop.close()


@celery_app.task(bind=True, name="rift.worker.tasks.render",
                 max_retries=0, acks_late=True, track_started=True)
def render(self, job_id: str, user_id: str, source_path: str, params: Dict[str, Any]):
    log.info(f"Render job {job_id} started")
    _update_job(job_id, status="processing", progress=1.0, message="Initializing...")

    temp_files = []
    writer = None

    def emit(pct, msg, **kw):
        _update_job(job_id, progress=pct, message=msg, **kw)

    try:
        effects = _get_effects()
        overlays = _get_overlays()
        upscaler = _get_upscaler()
        video_svc = _get_video()
        audio_svc = _get_audio()
        storage = _get_storage()

        emit(2.0, "Reading video metadata...")
        meta = video_svc.probe(source_path)
        log.info(f"Job {job_id}: {meta.width}x{meta.height} @ {meta.fps}fps {meta.frame_count}f")

        extract_fps = int(params.get("extract_fps", 10))
        output_fps  = int(params.get("output_fps", 30))
        target_res  = params.get("target_resolution", "Original")
        up_model    = params.get("upscale_model", "RealESRGAN_x4plus")
        out_fmt     = params.get("output_format", "mp4")
        effect_name = params.get("ink_technique", "none")
        overlay_name= params.get("overlay_type", "none")
        inc_audio   = bool(params.get("include_audio", True))
        norm_audio  = bool(params.get("normalize_audio", False))
        custom_audio= params.get("custom_audio_path")

        out_w, out_h, up_method, _ = _upscale_dims(meta.width, meta.height, target_res)
        extract_interval = max(1, int(round(meta.fps / extract_fps)))
        frames_to_process = meta.frame_count // extract_interval
        repeat_factor = max(1, int(round(output_fps / extract_fps)))
        total_out_frames = frames_to_process * repeat_factor

        emit(3.0, f"Output: {out_w}x{out_h} @ {output_fps}fps | {effect_name}", frames_total=total_out_frames)

        if up_method == "ai" and upscaler.gpu_available:
            emit(5.0, f"Loading AI model {up_model}...")
            ok, msg = upscaler.load(up_model)
            if not ok:
                log.warning(f"AI upscale unavailable: {msg}, using Lanczos")
                up_method = "lanczos"

        temp_dir = settings.STORAGE_ROOT / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_raw = str(temp_dir / f"raw_{job_id}.mp4")
        temp_files.append(temp_raw)

        emit(8.0, "Opening video writer...")
        writer = video_svc.open_writer(temp_raw, out_w, out_h, float(output_fps))

        cap = cv2.VideoCapture(source_path)
        if not cap.isOpened():
            raise Exception(f"Cannot open source: {source_path}")

        fi = 0
        extracted = 0
        frames_written = 0
        t_last = time.time()
        fps_since_update = 0

        while extracted < frames_to_process:
            ret, frame = cap.read()
            if not ret:
                break
            if fi % extract_interval == 0:
                # Upscale
                if up_method != "none":
                    frame = upscaler.upscale(frame, out_w, out_h, up_method)
                elif frame.shape[1] != out_w or frame.shape[0] != out_h:
                    frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

                # Effect
                if effect_name not in ("none", None, ""):
                    frame = effects.apply(frame, effect_name, params)

                # Overlay
                if overlay_name not in ("none", None, ""):
                    frame = overlays.apply(frame, overlay_name, params)

                # Write repeated frames for fps matching
                for _ in range(repeat_factor):
                    if not video_svc.write_frame(writer, frame, out_w, out_h):
                        raise Exception("FFmpeg pipe broken")
                    frames_written += 1

                extracted += 1
                fps_since_update += 1

                # Periodic GC
                if extracted % 20 == 0:
                    gc.collect()
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                # Progress update every 2s
                now = time.time()
                if now - t_last >= 2.0:
                    rfps = fps_since_update / (now - t_last)
                    eta = int((frames_to_process - extracted) / rfps) if rfps > 0 else 0
                    pct = 8.0 + (extracted / frames_to_process) * 80.0
                    emit(pct, f"{extracted}/{frames_to_process} frames | {rfps:.1f}fps | ETA {eta}s",
                         frames_done=extracted, render_fps=round(rfps, 2))
                    fps_since_update = 0
                    t_last = now

            fi += 1

        cap.release()
        ok = video_svc.close_writer(writer, timeout=180)
        writer = None
        if not ok or not os.path.exists(temp_raw) or os.path.getsize(temp_raw) == 0:
            raise Exception("FFmpeg produced empty output")

        # Audio
        emit(90.0, "Merging audio...")
        temp_final = str(temp_dir / f"final_{job_id}.mp4")
        temp_files.append(temp_final)
        audio_merged = False

        if inc_audio and audio_svc.ok:
            audio_src = custom_audio if custom_audio and os.path.exists(custom_audio) else (source_path if meta.has_audio else None)
            if audio_src:
                ok2, res = audio_svc.merge(temp_raw, audio_src, temp_final,
                                           meta.fps, float(output_fps), norm_audio)
                if ok2:
                    audio_merged = True
                else:
                    log.warning(f"Audio merge failed: {res}")

        if not audio_merged:
            shutil.copy2(temp_raw, temp_final)

        # Format conversion
        ultimate = temp_final
        if out_fmt.lower() != "mp4" and audio_svc.ok:
            temp_converted = str(temp_dir / f"converted_{job_id}.{out_fmt}")
            temp_files.append(temp_converted)
            ok3, res3 = audio_svc.convert(temp_final, temp_converted, out_fmt, inc_audio)
            if ok3:
                ultimate = temp_converted
            else:
                log.warning(f"Format conversion failed, keeping mp4: {res3}")

        emit(97.0, "Saving output...")
        from rift.services.storage import get_storage
        storage = get_storage()
        out_filename = f"RIFT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id[:8].replace('-','')}.{out_fmt}"
        stored_path, stored_size = storage.save_render(user_id, ultimate, out_filename)

        log.info(f"Job {job_id} complete: {stored_size/1e6:.2f}MB")

        _update_job(job_id,
            status="complete", progress=100.0, message="Render complete",
            frames_done=extracted, out_path=stored_path, out_filename=out_filename,
            out_size_bytes=stored_size, out_width=out_w, out_height=out_h,
            out_fps=float(output_fps), out_format=out_fmt,
        )

        # Email notification
        try:
            async def _mail():
                from rift.core.database import session_factory
                from rift.models import User
                from rift.services.email import EmailService
                async with session_factory()() as sess:
                    user = await sess.get(User, user_id)
                    if user:
                        await EmailService().send_render_complete(user.email, job_id, out_filename)
            asyncio.new_event_loop().run_until_complete(_mail())
        except Exception:
            pass

        return {"job_id": job_id, "status": "complete", "output": stored_path}

    except Exception as exc:
        log.error(f"Job {job_id} failed: {exc}", exc_info=True)
        _update_job(job_id, status="failed", error=traceback.format_exc()[:4000],
                    message=f"Failed: {str(exc)[:200]}")
        raise

    finally:
        if writer:
            try: video_svc.close_writer(writer, timeout=10)
            except: pass
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        for tf in temp_files:
            try:
                if os.path.exists(tf): os.remove(tf)
            except: pass


@celery_app.task(bind=True, name="rift.worker.tasks.preview",
                 soft_time_limit=90, time_limit=120)
def preview(self, user_id: str, video_path: str, frame_index: int, params: Dict[str, Any]):
    try:
        import base64
        from io import BytesIO
        from PIL import Image

        video_svc = _get_video()
        effects = _get_effects()
        overlays = _get_overlays()
        upscaler = _get_upscaler()

        meta = video_svc.probe(video_path)
        frame = video_svc.frame_at_index(video_path, frame_index)

        out_w, out_h, up_method, _ = _upscale_dims(meta.width, meta.height,
                                                     params.get("target_resolution", "Original"))

        if up_method == "ai" and upscaler.gpu_available:
            ok, _ = upscaler.load(params.get("upscale_model", "RealESRGAN_x4plus"))
            if ok:
                frame = upscaler.upscale(frame, out_w, out_h, "ai")
            else:
                frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
        elif up_method != "none":
            frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

        eff = params.get("ink_technique", "none")
        if eff not in ("none", None, ""):
            frame = effects.apply(frame, eff, params)

        ov = params.get("overlay_type", "none")
        if ov not in ("none", None, ""):
            frame = overlays.apply(frame, ov, params)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        if max(img.size) > 1280:
            img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()

        return {"success": True, "image": b64, "width": img.width,
                "height": img.height, "frame_index": frame_index}
    except Exception as e:
        log.error(f"Preview failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@celery_app.task(name="rift.worker.tasks.cleanup")
def cleanup():
    async def _do():
        from rift.core.database import session_factory
        from rift.models import Job, JobStatus
        from rift.services.storage import get_storage
        from sqlalchemy import select
        storage = get_storage()
        count = 0
        async with session_factory()() as sess:
            now = datetime.now(timezone.utc)
            res = await sess.execute(
                select(Job).where(Job.out_expires_at < now, Job.out_path.isnot(None))
            )
            for job in res.scalars():
                storage.delete(job.out_path)
                job.out_path = None
                job.out_filename = None
                count += 1
            await sess.commit()
        log.info(f"Cleaned {count} expired outputs")
    asyncio.new_event_loop().run_until_complete(_do())


@celery_app.task(name="rift.worker.tasks.reset_quotas")
def reset_quotas():
    async def _do():
        from rift.core.database import session_factory
        from rift.models import User
        from sqlalchemy import update
        now = datetime.now(timezone.utc)
        async with session_factory()() as sess:
            res = await sess.execute(
                update(User).where(User.quota_reset_date <= now).values(renders_this_month=0)
            )
            await sess.commit()
        log.info(f"Reset quotas for {res.rowcount} users")
    asyncio.new_event_loop().run_until_complete(_do())


def _get_storage():
    from rift.services.storage import get_storage
    return get_storage()