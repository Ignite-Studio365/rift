import gc
import threading
from pathlib import Path
from typing import Optional, Tuple
import cv2
import numpy as np
import torch
import logging

from rift.core.config import settings

log = logging.getLogger("rift.upscaler")

MODELS = {
    "RealESRGAN_x4plus":          {"url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",          "scale": 4, "num_block": 23},
    "RealESRGAN_x4plus_anime_6B": {"url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth","scale": 4, "num_block": 6},
    "RealESRGAN_x2plus":          {"url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",           "scale": 2, "num_block": 23},
}


class UpscalerService:
    def __init__(self):
        self.dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._model = None
        self._model_name: Optional[str] = None
        self._lock = threading.RLock()

    @property
    def gpu_available(self) -> bool:
        return self.dev.type == "cuda"

    def load(self, model_name: str) -> Tuple[bool, str]:
        with self._lock:
            if self._model_name == model_name and self._model is not None:
                return True, "Already loaded"
            if not self.gpu_available:
                return False, "No GPU available"
            cfg = MODELS.get(model_name)
            if not cfg:
                return False, f"Unknown model: {model_name}"
            try:
                from realesrgan import RealESRGANer
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from basicsr.utils.download_util import load_file_from_url
            except ImportError as e:
                return False, f"RealESRGAN not installed: {e}"
            wp = settings.STORAGE_ROOT / "weights" / f"{model_name}.pth"
            if not wp.exists():
                log.info(f"Downloading {model_name}...")
                try:
                    load_file_from_url(cfg["url"], model_dir=str(settings.STORAGE_ROOT / "weights"), progress=True)
                except Exception as e:
                    return False, f"Download failed: {e}"
            try:
                self._model = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                                num_block=cfg["num_block"], num_grow_ch=32, scale=cfg["scale"])
                self._model = RealESRGANer(
                    scale=cfg["scale"], model_path=str(wp), model=model,
                    device=self.dev, pre_pad=0, tile=256, tile_pad=10, half=False,
                )
                self._model_name = model_name
                log.info(f"Loaded {model_name} on {self.dev}")
                return True, "Loaded"
            except torch.cuda.OutOfMemoryError:
                self._model = None
                return False, "GPU OOM"
            except Exception as e:
                self._model = None
                return False, str(e)

    def upscale(self, frame: np.ndarray, target_w: int, target_h: int,
                method: str = "lanczos") -> np.ndarray:
        if method == "none" or (frame.shape[1] == target_w and frame.shape[0] == target_h):
            return frame
        if method == "ai" and self._model is not None:
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with torch.no_grad():
                    enhanced, _ = self._model.enhance(rgb, outscale=1)
                result = cv2.cvtColor(enhanced, cv2.COLOR_RGB2BGR)
                if result.shape[1] != target_w or result.shape[0] != target_h:
                    result = cv2.resize(result, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
                return result
            except torch.cuda.OutOfMemoryError:
                gc.collect()
                torch.cuda.empty_cache()
                log.warning("GPU OOM in upscale, falling back to Lanczos")
            except Exception as e:
                log.warning(f"AI upscale failed, falling back: {e}")
        return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

    def unload(self):
        with self._lock:
            self._model = None
            self._model_name = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def gpu_info(self) -> dict:
        if not torch.cuda.is_available():
            return {"available": False, "name": "CPU only"}
        props = torch.cuda.get_device_properties(0)
        return {
            "available": True,
            "name": props.name,
            "memory_gb": round(props.total_memory / 1e9, 2),
            "allocated_mb": round(torch.cuda.memory_allocated(0) / 1e6, 1),
            "model_loaded": self._model_name,
        }