from typing import Any, Dict, Optional
import numpy as np
import cv2
import torch
import torch.nn.functional as F
import kornia.filters as KF
import logging
from rift.services.effects import _dev, _to_tensor, _to_bgr, _gray, _hex, _color_tensor, _noise, _ensure_bgr

log = logging.getLogger("rift.overlays")


class Overlays:
    def __init__(self, device: Optional[torch.device] = None):
        self.dev = device or _dev()
        self._c: Dict = {}

    def _cache(self, k, fn):
        if k not in self._c: self._c[k] = fn()
        return self._c[k]

    def film_grain(self, f: np.ndarray, intensity=0.5, grain_size=50.0, color_temp=0.0) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        sigma = max(0.5, grain_size / 30.0)
        ks = max(3, int(sigma * 4) | 1)
        g = torch.Generator(device=self.dev)
        g.manual_seed(int(torch.randint(0, 100000, (1,)).item()))
        raw = torch.randn(1, 1, h, w, generator=g, device=self.dev)
        smooth = KF.gaussian_blur2d(raw, (ks, ks), (sigma, sigma))
        mn, mx = smooth.amin(), smooth.amax()
        norm = torch.pow((smooth - mn) / (mx - mn + 1e-8), 0.8)
        gray = _gray(img)
        dist = torch.exp(-4.0 * (gray - 0.5) ** 2)
        weighted = norm * (0.5 + 0.5 * dist)
        centered = (weighted - 0.5) * 2.0
        t = float(color_temp)
        if abs(t) > 0.01:
            scale = torch.tensor(
                [1+t*0.25, 1+t*0.10, 1-t*0.15] if t > 0 else [1-abs(t)*0.15, 1+abs(t)*0.10, 1+abs(t)*0.25],
                device=self.dev).view(1, 3, 1, 1)
            grain = centered.expand(1, 3, h, w) * scale
        else:
            grain = centered.expand(1, 3, h, w)
        return _to_bgr(torch.clamp(img + grain * 0.08 * intensity, 0, 1))

    def scan_lines(self, f: np.ndarray, intensity=0.4, line_spacing=4, line_thickness=1,
                   bloom=0.2) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        ls, lt = max(2, int(line_spacing)), max(1, int(line_thickness))
        mask = torch.ones(h, w, device=self.dev)
        for lp in range(0, h, ls):
            s, e = max(0, lp - lt//2), min(h, lp + lt//2 + 1)
            mask[s:e, :] = 1.0 - intensity
        ks = max(1, int(lt * 1.5) | 1)
        mask = KF.gaussian_blur2d(mask.unsqueeze(0).unsqueeze(0), (ks, 1), (lt*0.5, 0.01)).squeeze()
        if bloom > 0:
            gray = _gray(img).squeeze()
            bright = torch.clamp(gray - 0.78 + bloom * 0.12, 0, 1)
            bks = max(3, int(bloom * 15) * 2 + 1)
            glow = KF.gaussian_blur2d(bright.unsqueeze(0).unsqueeze(0), (bks, bks), (bloom*8, bloom*8)).squeeze()
            mask = mask * (0.6 + 0.4 * (1.0 - glow * bloom * 0.4))
        return _to_bgr(torch.clamp(img * mask.unsqueeze(0).unsqueeze(0).expand(1, 3, h, w), 0, 1))

    def cross_hatch_overlay(self, f: np.ndarray, intensity=0.3, spacing=10,
                             angle1=45.0, angle2=135.0) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        ys = torch.arange(h, dtype=torch.float32, device=self.dev)
        xs = torch.arange(w, dtype=torch.float32, device=self.dev)
        Y, X = torch.meshgrid(ys, xs, indexing="ij")
        hatch = torch.zeros(h, w, device=self.dev)
        for ang in [angle1, angle2]:
            ar = float(np.deg2rad(ang))
            dist = X * float(np.cos(ar)) + Y * float(np.sin(ar))
            dm = dist % float(spacing)
            hatch = torch.clamp(hatch + (torch.abs(dm - spacing/2) < 0.5).float(), 0, 1)
        return _to_bgr(torch.clamp(img * (1 - hatch.unsqueeze(0).unsqueeze(0).expand(1, 3, h, w) * intensity), 0, 1))

    def glass(self, f: np.ndarray, intensity=0.3, distortion=0.1, aberration=0.05,
              refraction=1.5, frost=0.2) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        nsh, nsw = max(4, h//16), max(4, w//16)
        g1 = torch.Generator(device=self.dev); g1.manual_seed(42)
        g2 = torch.Generator(device=self.dev); g2.manual_seed(43)
        nx = KF.gaussian_blur2d(F.interpolate(torch.randn(1, 1, nsh, nsw, generator=g1, device=self.dev) * distortion * 0.4, (h, w), mode="bicubic", align_corners=False), (5, 5), (2.0, 2.0))
        ny = KF.gaussian_blur2d(F.interpolate(torch.randn(1, 1, nsh, nsw, generator=g2, device=self.dev) * distortion * 0.4, (h, w), mode="bicubic", align_corners=False), (5, 5), (2.0, 2.0))
        scale = refraction * distortion * 35.0
        ys = torch.linspace(-1, 1, h, device=self.dev)
        xs = torch.linspace(-1, 1, w, device=self.dev)
        gy, gx = torch.meshgrid(ys, xs, indexing="ij")
        base = torch.stack([gx, gy], -1).unsqueeze(0)
        if aberration > 0:
            channels = []
            for ci in range(3):
                af = 1.0 + aberration * (0.6 - ci * 0.25)
                gc = base.clone()
                gc[0, :, :, 0] = torch.clamp(gc[0, :, :, 0] + nx.squeeze() * scale * af / (w/2), -1, 1)
                gc[0, :, :, 1] = torch.clamp(gc[0, :, :, 1] + ny.squeeze() * scale * af / (h/2), -1, 1)
                channels.append(F.grid_sample(img[:, ci:ci+1], gc, mode="bicubic", padding_mode="reflection", align_corners=True))
            distorted = torch.cat(channels, 1)
        else:
            wg = base.clone()
            wg[0, :, :, 0] = torch.clamp(wg[0, :, :, 0] + nx.squeeze() * scale / (w/2), -1, 1)
            wg[0, :, :, 1] = torch.clamp(wg[0, :, :, 1] + ny.squeeze() * scale / (h/2), -1, 1)
            distorted = F.grid_sample(img, wg, mode="bicubic", padding_mode="reflection", align_corners=True)
        if frost > 0:
            ft = self._cache(f"frost_{h}_{w}_{frost:.2f}", lambda: torch.clamp(
                torch.abs(_noise(h, w, frost*8, 77, self.dev)), 0, 1))
            fm = ft.expand(1, 3, h, w)
            blur = KF.gaussian_blur2d(distorted, (5, 5), (frost*6, frost*6))
            distorted = distorted * (1 - fm * frost) + blur * fm * frost
        return _to_bgr(torch.clamp(img * (1 - intensity) + distorted * intensity, 0, 1))

    def ocean_ripple(self, f: np.ndarray, intensity=0.3, wavelength=20.0, amplitude=10.0,
                     turbulence=0.2, drop_ripples=0.0) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        ys = torch.arange(h, dtype=torch.float32, device=self.dev)
        xs = torch.arange(w, dtype=torch.float32, device=self.dev)
        Y, X = torch.meshgrid(ys, xs, indexing="ij")
        wf = 2 * np.pi / max(wavelength, 1.0)
        rx = torch.sin(X * wf * 0.02) * amplitude * 0.6 + torch.sin(X * wf * 1.7 * 0.015 + Y * 0.01) * amplitude * 0.35
        ry = torch.cos(Y * wf * 0.02) * amplitude * 0.6 + torch.cos(Y * wf * 1.7 * 0.015 + X * 0.01) * amplitude * 0.35
        if turbulence > 0:
            for scale in [16, 32, 64]:
                th, tw = max(2, h//scale), max(2, w//scale)
                g1 = torch.Generator(device=self.dev); g1.manual_seed(scale)
                g2 = torch.Generator(device=self.dev); g2.manual_seed(scale+1)
                tx = F.interpolate(torch.randn(1, 1, th, tw, generator=g1, device=self.dev) * turbulence * 1.5, (h, w), mode="bilinear", align_corners=False).squeeze()
                ty = F.interpolate(torch.randn(1, 1, th, tw, generator=g2, device=self.dev) * turbulence * 1.5, (h, w), mode="bilinear", align_corners=False).squeeze()
                rx += tx * (1.0 / (scale / 16))
                ry += ty * (1.0 / (scale / 16))
        bxs = torch.linspace(-1, 1, w, device=self.dev)
        bys = torch.linspace(-1, 1, h, device=self.dev)
        bgy, bgx = torch.meshgrid(bys, bxs, indexing="ij")
        wg = torch.stack([torch.clamp(bgx + rx * intensity / (w/2), -1, 1),
                          torch.clamp(bgy + ry * intensity / (h/2), -1, 1)], -1).unsqueeze(0)
        return _to_bgr(torch.clamp(F.grid_sample(img, wg, mode="bicubic", padding_mode="reflection", align_corners=True), 0, 1))

    def bokeh(self, f: np.ndarray, intensity=0.4, bokeh_size=3.0, shape="circular",
              highlight_boost=0.3) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        thresh = 0.78 - highlight_boost * 0.08
        hm = torch.clamp(gray - thresh, 0, 1) / (1 - thresh + 1e-8)
        ks = max(3, int(bokeh_size * 18) | 1)
        kc = ks // 2
        ky_k = torch.arange(ks, dtype=torch.float32, device=self.dev)
        kx_k = torch.arange(ks, dtype=torch.float32, device=self.dev)
        KY, KX = torch.meshgrid(ky_k, kx_k, indexing="ij")
        dk = torch.sqrt((KX - kc) ** 2 + (KY - kc) ** 2)
        kern = (dk <= kc * (0.8 if shape == "hexagonal" else 0.9 if shape == "octagonal" else 1.0)).float()
        if shape == "circular":
            edge = (dk > kc - 1) & (dk <= kc)
            kern = (dk <= kc - 1).float()
            kern[edge] = 1.0 - (dk[edge] - (kc - 1))
        ks_sum = kern.sum()
        if ks_sum > 0: kern = kern / ks_sum
        blurred = F.conv2d(hm.unsqueeze(0).unsqueeze(0), kern.unsqueeze(0).unsqueeze(0), padding=kc).squeeze()
        bmx = blurred.amax()
        if bmx > 0: blurred = blurred / bmx
        bright_pix = img.squeeze()[:, gray > 0.3]
        avg_color = bright_pix.mean(1) * 1.2 if bright_pix.numel() > 0 else torch.tensor([0.9, 0.94, 0.96], device=self.dev)
        bokeh_layer = (blurred.unsqueeze(0) * avg_color.view(3, 1, 1)).unsqueeze(0).clamp(0, 1)
        screen = 1.0 - (1.0 - img) * (1.0 - bokeh_layer)
        return _to_bgr(torch.clamp(img * (1 - intensity * 0.65) + screen * intensity * 0.65, 0, 1))

    def texture(self, f: np.ndarray, intensity=0.4, texture_type="paper", scale=1.0,
                contrast=1.0) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        tk = f"tex_{texture_type}_{h}_{w}_{scale:.2f}_{contrast:.2f}"
        tex = self._cache(tk, lambda: self._gen_texture(texture_type, h, w, contrast))
        t3 = tex.expand(1, 3, h, w)
        overlay = torch.where(t3 < 0.5, 2.0 * img * t3, 1.0 - 2.0 * (1.0 - img) * (1.0 - t3))
        imap = intensity * (0.8 + 0.2 * _gray(img))
        return _to_bgr(torch.clamp(img * (1 - imap * 0.3) + overlay * imap * 0.7, 0, 1))

    def _gen_texture(self, tt: str, h: int, w: int, contrast: float) -> torch.Tensor:
        tex = torch.zeros(1, 1, h, w, device=self.dev)
        if tt == "paper":
            for s, seed, wt in [(4, 10, 1.0), (8, 11, 0.5), (16, 12, 0.25), (32, 13, 0.125)]:
                g = torch.Generator(device=self.dev); g.manual_seed(seed)
                n = torch.randn(1, 1, max(2, h//s), max(2, w//s), generator=g, device=self.dev) * 0.6
                tex += F.interpolate(n, (h, w), mode="bicubic", align_corners=False) * wt
            tex = KF.gaussian_blur2d(tex, (3, 3), (0.8, 0.8))
        elif tt == "canvas":
            ys = torch.arange(h, device=self.dev); xs = torch.arange(w, device=self.dev)
            Y, X = torch.meshgrid(ys, xs, indexing="ij")
            tex = ((X % 12 < 3).float() * 0.5 + (Y % 12 < 3).float() * 0.4).unsqueeze(0).unsqueeze(0)
            g = torch.Generator(device=self.dev); g.manual_seed(20)
            tex = KF.gaussian_blur2d(tex + torch.randn(1, 1, h, w, generator=g, device=self.dev) * 0.12, (3, 3), (0.5, 0.5))
        elif tt == "wood":
            ys = torch.arange(h, dtype=torch.float32, device=self.dev); xs = torch.arange(w, dtype=torch.float32, device=self.dev)
            Y, X = torch.meshgrid(ys, xs, indexing="ij")
            dist = torch.sqrt((X - w/2) ** 2 + (Y - h/2) ** 2)
            tex = (torch.sin(dist / 8) * 0.4 + 0.5).unsqueeze(0).unsqueeze(0)
            g = torch.Generator(device=self.dev); g.manual_seed(40)
            tex = KF.gaussian_blur2d(tex + torch.randn(1, 1, h, w, generator=g, device=self.dev) * 0.15, (3, 3), (1.0, 1.0))
        else:
            g = torch.Generator(device=self.dev); g.manual_seed(60)
            tex = torch.randn(1, 1, h, w, generator=g, device=self.dev) * 0.3 + 0.5
        mn, mx = tex.amin(), tex.amax()
        tex = (tex - mn) / (mx - mn + 1e-8)
        if abs(contrast - 1.0) > 0.01:
            m = tex.mean()
            tex = torch.clamp((tex - m) * contrast + m, 0, 1)
        return tex

    def apply(self, frame: np.ndarray, name: str, p: Dict[str, Any]) -> np.ndarray:
        if frame is None or frame.size == 0: return frame
        frame = _ensure_bgr(frame)
        try:
            if name == "film_grain":
                return self.film_grain(frame, p.get("overlay_intensity", 0.5), p.get("overlay_grain_size", 50.0), p.get("overlay_color_temp", 0.0))
            elif name == "scan_lines":
                return self.scan_lines(frame, p.get("overlay_intensity", 0.4), p.get("overlay_line_spacing", 4), p.get("overlay_line_thickness", 1), p.get("overlay_bloom", 0.2))
            elif name == "cross_hatch":
                return self.cross_hatch_overlay(frame, p.get("overlay_intensity", 0.3), p.get("overlay_spacing", 10), p.get("overlay_angle1", 45.0), p.get("overlay_angle2", 135.0))
            elif name == "glass":
                return self.glass(frame, p.get("overlay_intensity", 0.3), p.get("overlay_distortion", 0.1), p.get("overlay_aberration", 0.05), p.get("overlay_refraction", 1.5), p.get("overlay_frost", 0.2))
            elif name == "ocean_ripple":
                return self.ocean_ripple(frame, p.get("overlay_intensity", 0.3), p.get("overlay_wavelength", 20.0), p.get("overlay_amplitude", 10.0), p.get("overlay_turbulence", 0.2), p.get("overlay_drop_ripples", 0.0))
            elif name == "bokeh":
                return self.bokeh(frame, p.get("overlay_intensity", 0.4), p.get("overlay_bokeh_size", 3.0), p.get("overlay_bokeh_shape", "circular"), p.get("overlay_highlight_boost", 0.3))
            elif name == "texture":
                return self.texture(frame, p.get("overlay_intensity", 0.4), p.get("overlay_texture_type", "paper"), p.get("overlay_scale", 1.0), p.get("overlay_texture_contrast", 1.0))
            elif name in ("none", None, ""):
                return frame
            else:
                return frame
        except Exception as e:
            log.error(f"Overlay {name} failed: {e}", exc_info=True)
            return frame

    def clear(self):
        self._c.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()