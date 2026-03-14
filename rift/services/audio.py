import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple
import logging

log = logging.getLogger("rift.audio")


class AudioService:
    def __init__(self):
        self.ok = self._check("ffmpeg")
        self.probe_ok = self._check("ffprobe")

    def _check(self, bin: str) -> bool:
        try:
            return subprocess.run([bin, "-version"], capture_output=True, timeout=5).returncode == 0
        except Exception:
            return False

    def has_audio_stream(self, path: str) -> bool:
        if not self.probe_ok:
            return False
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

    def merge(self, video: str, audio_src: str, out: str,
              src_fps: float, out_fps: float, normalize: bool = False,
              timeout: int = 600) -> Tuple[bool, str]:
        if not self.ok:
            return False, "FFmpeg not available"
        tmp_clean = out + "_clean.wav"
        tmp_tempo = out + "_tempo.wav"
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", audio_src, "-acodec", "pcm_s16le",
                 "-ar", "48000", "-ac", "2", tmp_clean],
                capture_output=True, text=True, timeout=timeout // 2,
            )
            if r.returncode != 0:
                return False, f"Audio conv failed: {r.stderr[-200:]}"

            audio_to_use = tmp_clean
            speed = out_fps / src_fps if src_fps > 0 else 1.0
            if abs(speed - 1.0) > 0.01:
                parts = []
                rem = speed
                while rem > 2.0:
                    parts.append("atempo=2.0"); rem /= 2.0
                while rem < 0.5:
                    parts.append("atempo=0.5"); rem /= 0.5
                if abs(rem - 1.0) > 0.01:
                    parts.append(f"atempo={rem:.4f}")
                if parts:
                    tr = subprocess.run(
                        ["ffmpeg", "-y", "-i", tmp_clean, "-filter:a", ",".join(parts),
                         "-c:a", "pcm_s16le", tmp_tempo],
                        capture_output=True, text=True, timeout=timeout // 2,
                    )
                    if tr.returncode == 0 and os.path.exists(tmp_tempo):
                        audio_to_use = tmp_tempo

            cmd = ["ffmpeg", "-y", "-i", video, "-i", audio_to_use, "-c:v", "copy"]
            if normalize:
                cmd += ["-c:a", "aac", "-filter:a", "loudnorm=I=-16:LRA=11:TP=-1.5", "-b:a", "192k"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "192k", "-strict", "experimental"]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest", "-movflags", "+faststart", out]

            mr = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if mr.returncode != 0:
                return False, f"Merge failed: {mr.stderr[-300:]}"
            if not os.path.exists(out) or os.path.getsize(out) == 0:
                return False, "Merged file empty"
            return True, out
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except Exception as e:
            return False, str(e)
        finally:
            for tf in [tmp_clean, tmp_tempo]:
                if os.path.exists(tf):
                    try: os.remove(tf)
                    except: pass

    def convert(self, inp: str, out: str, fmt: str, keep_audio: bool = True,
                timeout: int = 600) -> Tuple[bool, str]:
        if not self.ok:
            return False, "FFmpeg not available"
        FMT = {
            "mp4": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            "mov": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            "avi": ["-c:v", "mpeg4", "-b:v", "20M"],
            "mkv": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"],
            "webm": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30", "-deadline", "good"],
        }
        cmd = ["ffmpeg", "-y", "-i", inp] + FMT.get(fmt, FMT["mp4"])
        if keep_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k"] if fmt != "webm" else ["-c:a", "libvorbis", "-q:a", "5"]
        else:
            cmd += ["-an"]
        cmd.append(out)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                return False, f"Convert failed: {r.stderr[-200:]}"
            return True, out
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except Exception as e:
            return False, str(e)