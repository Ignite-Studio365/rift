import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
import cv2
import numpy as np
import logging

from rift.core.exceptions import VideoError

log = logging.getLogger("rift.video")


@dataclass
class VideoMeta:
    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    codec: Optional[str]
    has_audio: bool
    audio_codec: Optional[str]
    size_bytes: int

    def to_dict(self):
        return {
            "width": self.width, "height": self.height,
            "fps": round(self.fps, 3), "frame_count": self.frame_count,
            "duration": round(self.duration, 3), "codec": self.codec,
            "has_audio": self.has_audio, "size_bytes": self.size_bytes,
        }


class VideoService:
    def probe(self, path: str) -> VideoMeta:
        p = Path(path)
        if not p.exists():
            raise VideoError(f"File not found: {path}")

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise VideoError(f"Cannot open video: {path}")
        try:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
            fc = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cc = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = bytearray([cc & 0xFF, (cc >> 8) & 0xFF, (cc >> 16) & 0xFF, (cc >> 24) & 0xFF]).decode("utf-8", "ignore").strip("\x00") or None
        finally:
            cap.release()

        if w <= 0 or h <= 0:
            raise VideoError(f"Invalid dimensions: {w}x{h}")

        has_audio, audio_codec = False, None
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                for s in json.loads(r.stdout).get("streams", []):
                    if s.get("codec_type") == "audio":
                        has_audio = True
                        audio_codec = s.get("codec_name")
        except Exception:
            pass

        return VideoMeta(
            path=path, width=w, height=h, fps=fps,
            frame_count=max(fc, 1), duration=fc / fps if fps > 0 else 0,
            codec=codec, has_audio=has_audio, audio_codec=audio_codec,
            size_bytes=p.stat().st_size,
        )

    def frame_at_time(self, path: str, seconds: float, max_w: Optional[int] = None) -> Tuple[np.ndarray, int]:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise VideoError(f"Cannot open: {path}")
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            idx = max(0, min(int(seconds * fps), total - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                raise VideoError(f"Cannot read frame {idx}")
            if max_w and frame.shape[1] > max_w:
                scale = max_w / frame.shape[1]
                frame = cv2.resize(frame, (max_w, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
            return frame, idx
        finally:
            cap.release()

    def frame_at_index(self, path: str, idx: int) -> np.ndarray:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise VideoError(f"Cannot open: {path}")
        try:
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            idx = max(0, min(idx, total - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                raise VideoError(f"Cannot read frame {idx}")
            return frame
        finally:
            cap.release()

    def open_writer(self, out_path: str, w: int, h: int, fps: float) -> subprocess.Popen:
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{w}x{h}", "-r", str(fps), "-pix_fmt", "bgr24", "-i", "-",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-movflags", "+faststart", out_path,
        ]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write_frame(self, proc: subprocess.Popen, frame: np.ndarray, w: int, h: int) -> bool:
        if frame is None or frame.size == 0:
            return False
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        if len(frame.shape) == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.shape[1] != w or frame.shape[0] != h:
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LANCZOS4)
        try:
            proc.stdin.write(frame.tobytes())
            return True
        except BrokenPipeError:
            return False

    def close_writer(self, proc: subprocess.Popen, timeout: int = 120) -> bool:
        try:
            proc.stdin.close()
            proc.wait(timeout=timeout)
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            return False

    def has_audio(self, path: str) -> bool:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_type",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=15,
            )
            return r.returncode == 0 and r.stdout.strip() == "audio"
        except Exception:
            return False

    def verify(self, path: str) -> Tuple[bool, str]:
        if not Path(path).exists():
            return False, "File not found"
        if Path(path).stat().st_size == 0:
            return False, "File is empty"
        cap = cv2.VideoCapture(path)
        try:
            if not cap.isOpened():
                return False, "Cannot open file"
            ret, _ = cap.read()
            return (True, "OK") if ret else (False, "Cannot read first frame")
        finally:
            cap.release()