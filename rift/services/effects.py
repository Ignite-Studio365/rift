from typing import Any, Dict, Optional, Tuple
import numpy as np
import cv2
import torch
import torch.nn.functional as F
import kornia.filters as KF
import kornia.color as KC
import logging

log = logging.getLogger("rift.effects")


def _dev() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _to_tensor(bgr: np.ndarray, dev: torch.device) -> torch.Tensor:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return torch.from_numpy(rgb.astype(np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0).to(dev)


def _to_bgr(t: torch.Tensor) -> np.ndarray:
    arr = (t.squeeze(0).permute(1, 2, 0).clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _gray(t: torch.Tensor) -> torch.Tensor:
    return KC.rgb_to_grayscale(t)


def _hex(h: str) -> Tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0


def _color_tensor(h: str, sz: Tuple, dev: torch.device) -> torch.Tensor:
    r, g, b = _hex(h)
    return torch.tensor([[[r]], [[g]], [[b]]], dtype=torch.float32, device=dev).expand(1, 3, *sz)


def _noise(h: int, w: int, sigma: float, seed: int, dev: torch.device) -> torch.Tensor:
    g = torch.Generator(device=dev)
    g.manual_seed(seed)
    n = torch.randn(1, 1, h, w, generator=g, device=dev)
    ks = max(3, int(sigma * 4) | 1)
    return KF.gaussian_blur2d(n, (ks, ks), (sigma, sigma))


def _bayer(h: int, w: int, dev: torch.device) -> torch.Tensor:
    b = torch.tensor([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                     dtype=torch.float32, device=dev) / 16.0
    return b.repeat((h + 3) // 4, (w + 3) // 4)[:h, :w]


def _scharr(dev: torch.device) -> Tuple[torch.Tensor, torch.Tensor]:
    sx = torch.tensor([[[[-3, 0, 3], [-10, 0, 10], [-3, 0, 3]]]], dtype=torch.float32, device=dev)
    sy = torch.tensor([[[[-3, -10, -3], [0, 0, 0], [3, 10, 3]]]], dtype=torch.float32, device=dev)
    return sx, sy


def _ensure_bgr(f: np.ndarray) -> np.ndarray:
    if f is None or f.size == 0:
        return f
    if len(f.shape) == 2:
        return cv2.cvtColor(f, cv2.COLOR_GRAY2BGR)
    if f.shape[2] == 4:
        return cv2.cvtColor(f, cv2.COLOR_BGRA2BGR)
    return f


class Effects:
    def __init__(self, device: Optional[torch.device] = None):
        self.dev = device or _dev()
        self._c: Dict[str, torch.Tensor] = {}
        log.info(f"Effects engine on {self.dev}")

    def _cache(self, k: str, fn):
        if k not in self._c:
            self._c[k] = fn()
        return self._c[k]

    def graphic_pen(self, f: np.ndarray, density=0.8, stroke_length=12, angle=45.0,
                    ink_color="#000000", paper_color="#FFFFFF", contrast=1.3,
                    stroke_spacing=4.0) -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img)
        if contrast != 1.0:
            gray = torch.clamp(gray * contrast, 0, 1)
        dark = 1.0 - gray
        ar = float(np.deg2rad(angle))
        ca, sa = float(np.cos(ar)), float(np.sin(ar))
        ys = torch.arange(h, dtype=torch.float32, device=self.dev)
        xs = torch.arange(w, dtype=torch.float32, device=self.dev)
        Y, X = torch.meshgrid(ys, xs, indexing="ij")
        V = -X * sa + Y * ca
        disp = (_noise(h, w, 25.0, 42, self.dev).squeeze() - 0.5) * stroke_spacing * 1.3
        Vj = V + disp
        Vq = torch.round(Vj / stroke_spacing) * stroke_spacing
        on_scan = torch.abs(Vj - Vq) <= 1.5
        def _dblur(n, half):
            k = torch.zeros(half*2+1, half*2+1, device=self.dev)
            c = half
            for i in range(-half, half+1):
                kx = int(round(c + i*ca))
                ky = int(round(c + i*sa))
                if 0 <= kx < k.shape[1] and 0 <= ky < k.shape[0]:
                    k[ky, kx] = 1.0
            s = k.sum()
            if s > 0: k /= s
            out = F.conv2d(n, k.unsqueeze(0).unsqueeze(0), padding=half)
            mn, mx = out.amin(), out.amax()
            return (out - mn) / (mx - mn + 1e-8)
        nm = _dblur(_noise(h, w, 2.0, 43, self.dev), 8).squeeze()
        ns = _dblur(_noise(h, w, 1.0, 44, self.dev), 3).squeeze()
        nu = (nm * 0.75 + ns * 0.25)
        nu = (nu - nu.amin()) / (nu.amax() - nu.amin() + 1e-8)
        bk = torch.clamp(torch.pow(dark.squeeze() + 1e-8, 0.62) * 0.72, 0, 0.68)
        mask = (on_scan & (nu < bk)).float().unsqueeze(0)
        ir, ig, ib = _hex(ink_color)
        pr, pg, pb = _hex(paper_color)
        ink = torch.tensor([ir, ig, ib], device=self.dev).view(3, 1, 1)
        paper = torch.tensor([pr, pg, pb], device=self.dev).view(3, 1, 1)
        return _to_bgr((paper * (1 - mask) + ink * mask).unsqueeze(0))

    def cross_hatch(self, f: np.ndarray, density=0.8, stroke_length=10, angle1=45.0,
                    angle2=135.0, ink_color="#000000", paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        mn, mx = gray.amin(), gray.amax()
        if (mx - mn) < 0.1: gray = (gray - mn) / (mx - mn + 1e-8)
        dark = torch.clamp(torch.pow(1.0 - gray + 1e-8, 0.8), 0, 1)
        ys = torch.arange(h, dtype=torch.float32, device=self.dev)
        xs = torch.arange(w, dtype=torch.float32, device=self.dev)
        Y, X = torch.meshgrid(ys, xs, indexing="ij")
        sp = max(2.0, 8.0 / max(density, 0.5))
        ir, ig, ib = _hex(ink_color)
        pr, pg, pb = _hex(paper_color)
        res = torch.stack([torch.full((h, w), pr, device=self.dev),
                           torch.full((h, w), pg, device=self.dev),
                           torch.full((h, w), pb, device=self.dev)], 0)
        for idx, (ang, wt) in enumerate([(angle1, 1.0), (angle2, 0.8)]):
            ar = float(np.deg2rad(ang))
            dist = X * float(np.cos(ar)) + Y * float(np.sin(ar))
            dm = dist % sp
            line = (torch.abs(dm - sp / 2) < 0.8).float()
            g2 = torch.Generator(device=self.dev); g2.manual_seed(100 + idx)
            rand = torch.rand(h, w, generator=g2, device=self.dev)
            stroke = line * (rand < dark * density * wt).float()
            ink_l = torch.stack([torch.full((h, w), ir, device=self.dev),
                                  torch.full((h, w), ig, device=self.dev),
                                  torch.full((h, w), ib, device=self.dev)], 0)
            s3 = stroke.unsqueeze(0).expand(3, -1, -1)
            res = res * (1 - s3) + ink_l * s3
        gnp = (gray.cpu().numpy() * 255).astype(np.uint8)
        edges = torch.from_numpy(cv2.Canny(gnp, 50, 150).astype(np.float32) / 255.0).to(self.dev)
        e3 = edges.unsqueeze(0).expand(3, -1, -1)
        ink_f = torch.stack([torch.full((h, w), ir, device=self.dev),
                              torch.full((h, w), ig, device=self.dev),
                              torch.full((h, w), ib, device=self.dev)], 0)
        res = torch.clamp(res * (1 - e3) + ink_f * e3, 0, 1)
        return _to_bgr(res.unsqueeze(0))

    def pencil_sketch(self, f: np.ndarray, intensity=0.8, ink_color="#000000",
                      paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img)
        inv = 1.0 - gray
        blur = KF.gaussian_blur2d(inv, (21, 21), (3.5, 3.5))
        sketch = torch.clamp(gray / (1.0 - blur + 1e-8), 0, 1)
        noise = self._cache(f"pencil_{h}_{w}", lambda: KF.gaussian_blur2d(
            torch.randn(1, 1, h, w, device=self.dev) * 0.04, (3, 3), (0.8, 0.8)))
        sketch = torch.clamp(sketch + noise * intensity, 0, 1).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        return _to_bgr(torch.clamp(paper * (1 - sketch) + ink * sketch, 0, 1))

    def stipple(self, f: np.ndarray, density=0.8, dot_size_min=1, dot_size_max=3,
                ink_color="#000000", paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        prob = (1.0 - gray) * density
        g = torch.Generator(device=self.dev); g.manual_seed(55)
        rand = torch.rand(h, w, generator=g, device=self.dev)
        dot_base = (rand < prob).float()
        ir, ig, ib = _hex(ink_color); pr, pg, pb = _hex(paper_color)
        canvas = torch.stack([torch.full((h, w), pr, device=self.dev),
                               torch.full((h, w), pg, device=self.dev),
                               torch.full((h, w), pb, device=self.dev)], 0)
        ink_f = torch.stack([torch.full((h, w), ir, device=self.dev),
                              torch.full((h, w), ig, device=self.dev),
                              torch.full((h, w), ib, device=self.dev)], 0)
        for r in range(dot_size_max, dot_size_min - 1, -1):
            dark = (prob > 0.2 * r).float()
            dots = dot_base * dark
            ks = r * 2 + 1
            ky = torch.arange(ks, dtype=torch.float32, device=self.dev)
            kx = torch.arange(ks, dtype=torch.float32, device=self.dev)
            KY, KX = torch.meshgrid(ky, kx, indexing="ij")
            circle = ((KX - r) ** 2 + (KY - r) ** 2 <= r ** 2).float()
            cs = circle.sum()
            if cs > 0: circle /= cs
            dil = F.conv2d(dots.unsqueeze(0).unsqueeze(0), circle.unsqueeze(0).unsqueeze(0), padding=r).squeeze()
            mask = (dil > 0.1).float().unsqueeze(0).expand(3, -1, -1)
            canvas = canvas * (1 - mask) + ink_f * mask
        return _to_bgr(torch.clamp(canvas.unsqueeze(0), 0, 1))

    def woodcut(self, f: np.ndarray, levels=4, edge_strength=0.7,
                ink_color="#000000", paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img)
        levels = max(2, min(8, int(levels)))
        step = 1.0 / levels
        post = torch.round(gray / step) * step
        blur = KF.gaussian_blur2d(post, (3, 3), (0.8, 0.8))
        sx, sy = _scharr(self.dev)
        edges = torch.sqrt(KF.filter2d(blur, sx) ** 2 + KF.filter2d(blur, sy) ** 2)
        et = torch.quantile(edges.reshape(-1), 0.95) * edge_strength
        eb = (edges > et).float()
        inv = 1.0 - post
        st = torch.quantile(inv.reshape(-1), 0.70)
        sb = (inv > st).float()
        mask = torch.clamp(eb + sb, 0, 1).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        return _to_bgr(torch.clamp(paper * (1 - mask) + ink * mask, 0, 1))

    def halftone(self, f: np.ndarray, pattern="dot", frequency=10.0, angle=45.0,
                 ink_color="#000000", paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        ys = torch.arange(h, dtype=torch.float32, device=self.dev)
        xs = torch.arange(w, dtype=torch.float32, device=self.dev)
        Y, X = torch.meshgrid(ys, xs, indexing="ij")
        if pattern == "dot":
            ar = float(np.deg2rad(angle))
            u = (X * float(np.cos(ar)) - Y * float(np.sin(ar))) / frequency
            v = (X * float(np.sin(ar)) + Y * float(np.cos(ar))) / frequency
            screen = 0.5 + 0.5 * torch.sin(2 * np.pi * u) * torch.sin(2 * np.pi * v)
        elif pattern == "line":
            ar = float(np.deg2rad(angle))
            screen = 0.5 + 0.5 * torch.sin(2 * np.pi * (X * float(np.cos(ar)) + Y * float(np.sin(ar))) / frequency)
        else:
            dist = torch.sqrt((X - w/2) ** 2 + (Y - h/2) ** 2) / frequency
            screen = 0.5 + 0.5 * torch.sin(2 * np.pi * dist)
        mask = (screen < gray).float().unsqueeze(0).unsqueeze(0).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        return _to_bgr(torch.clamp(paper * (1 - mask) + ink * mask, 0, 1))

    def dither(self, f: np.ndarray, dither_type="floyd", intensity=0.8,
               ink_color="#000000", paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        if dither_type == "bayer":
            dithered = (gray > _bayer(h, w, self.dev)).float()
        elif dither_type == "threshold":
            dithered = (gray > 0.5).float()
        else:
            binary = (gray > 0.5).float()
            err = gray - binary
            r_e = F.pad(err[:, 1:], (0, 1)) * (7/16)
            bl_e = F.pad(err[:-1, :-1], (1, 0, 1, 0)) * (3/16)
            dn_e = F.pad(err[:-1, :], (0, 0, 1, 0)) * (5/16)
            br_e = F.pad(err[:-1, 1:], (0, 1, 1, 0)) * (1/16)
            dithered = (gray + r_e + bl_e + dn_e + br_e > 0.5).float()
        if intensity < 1.0:
            g2 = torch.Generator(device=self.dev); g2.manual_seed(33)
            dithered = torch.clamp(dithered + torch.rand(h, w, generator=g2, device=self.dev) * (1-intensity) * 0.2, 0, 1)
        mask = dithered.unsqueeze(0).unsqueeze(0).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        return _to_bgr(torch.clamp(paper * (1 - mask) + ink * mask, 0, 1))

    def engraving(self, f: np.ndarray, stroke_length=8, angle=45.0,
                  ink_color="#000000", paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        ys = torch.arange(h, dtype=torch.float32, device=self.dev)
        xs = torch.arange(w, dtype=torch.float32, device=self.dev)
        Y, X = torch.meshgrid(ys, xs, indexing="ij")
        ar = float(np.deg2rad(angle))
        dist = X * float(np.cos(ar)) + Y * float(np.sin(ar))
        lp = 0.5 + 0.5 * torch.sin(dist * 2 * np.pi / max(stroke_length, 1))
        inv = 1.0 - gray
        eng = (lp < 0.3 + inv * 0.4).float() * inv
        noise = self._cache(f"eng_{h}_{w}", lambda: torch.randn(h, w, device=self.dev) * 0.03)
        e3 = eng.unsqueeze(0).unsqueeze(0).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        res = paper * (1 - e3) + ink * e3 + noise.unsqueeze(0).unsqueeze(0).expand(1, 3, h, w)
        return _to_bgr(torch.clamp(res, 0, 1))

    def _bayer_levels(self, ch: torch.Tensor, levels: int) -> torch.Tensor:
        h, w = ch.shape
        bm = _bayer(h, w, self.dev)
        scale = 1.0 / (levels - 1.0 + 1e-8) * 0.5
        return torch.clamp(torch.round((ch + (bm - 0.5) * scale) * (levels - 1)) / (levels - 1), 0, 1)

    def color_bitmap(self, f: np.ndarray, levels=6, ink_color="#000000",
                     paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        levels = max(2, min(16, int(levels)))
        dithered = torch.stack([self._bayer_levels(img[0, c], levels) for c in range(3)], 0).unsqueeze(0)
        ir, ig, ib = _hex(ink_color); pr, pg, pb = _hex(paper_color)
        ink = torch.tensor([ir, ig, ib], device=self.dev).view(1, 3, 1, 1)
        paper = torch.tensor([pr, pg, pb], device=self.dev).view(1, 3, 1, 1)
        return _to_bgr(torch.clamp(paper + (ink - paper) * dithered, 0, 1))

    def grayscale_bitmap(self, f: np.ndarray, levels=6, ink_color="#000000",
                         paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img).squeeze()
        levels = max(2, min(16, int(levels)))
        dithered = self._bayer_levels(gray, levels).unsqueeze(0).unsqueeze(0).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        return _to_bgr(torch.clamp(paper * (1 - dithered) + ink * dithered, 0, 1))

    def charcoal(self, f: np.ndarray, intensity=0.8, ink_color="#000000",
                 paper_color="#FFFFFF") -> np.ndarray:
        f = _ensure_bgr(f)
        img = _to_tensor(f, self.dev)
        _, _, h, w = img.shape
        gray = _gray(img)
        charcoal = gray.clone()
        for div, strength in [(16, 0.4), (8, 0.3), (4, 0.2)]:
            sigma = float(h) / div
            ks = max(3, int(sigma * 2) | 1)
            noise = self._cache(f"ch_{div}_{h}_{w}", lambda d=div, s=sigma, k=ks: KF.gaussian_blur2d(
                torch.randn(1, 1, h, w, device=self.dev) * strength, (k, k), (s/3, s/3)))
            charcoal = torch.clamp(charcoal + noise * 0.3 * intensity, 0, 1)
        fine = self._cache(f"ch_fine_{h}_{w}", lambda: torch.randn(1, 1, h, w, device=self.dev) * 0.15 * intensity)
        charcoal = KF.gaussian_blur2d(torch.clamp(charcoal + fine * 0.2, 0, 1), (3, 3), (0.5, 0.5))
        gnp = (gray.squeeze().cpu().numpy() * 255).astype(np.uint8)
        edges = torch.from_numpy(cv2.Canny(gnp, 50, 150).astype(np.float32) / 255.0).to(self.dev)
        charcoal = torch.clamp(charcoal + edges.unsqueeze(0).unsqueeze(0) * 0.2, 0, 1).expand(1, 3, h, w)
        ink = _color_tensor(ink_color, (h, w), self.dev)
        paper = _color_tensor(paper_color, (h, w), self.dev)
        return _to_bgr(torch.clamp(paper * (1 - charcoal) + ink * charcoal, 0, 1))

    def apply(self, frame: np.ndarray, name: str, p: Dict[str, Any]) -> np.ndarray:
        if frame is None or frame.size == 0:
            return frame
        frame = _ensure_bgr(frame)
        ink = p.get("ink_color", "#000000")
        paper = p.get("paper_color", "#FFFFFF")
        try:
            if name == "graphic_pen":
                return self.graphic_pen(frame, p.get("ink_density", 0.8), p.get("ink_length", 12),
                    p.get("ink_angle", 45.0), ink, paper, p.get("ink_contrast", 1.3), p.get("stroke_spacing", 4.0))
            elif name == "cross_hatch":
                return self.cross_hatch(frame, p.get("ink_density", 0.8), p.get("ink_length", 10),
                    p.get("ink_angle1", 45.0), p.get("ink_angle2", 135.0), ink, paper)
            elif name == "pencil_sketch":
                return self.pencil_sketch(frame, p.get("ink_density", 0.8), ink, paper)
            elif name == "stipple":
                return self.stipple(frame, p.get("ink_density", 0.8), p.get("dot_size_min", 1),
                    p.get("dot_size_max", 3), ink, paper)
            elif name == "woodcut":
                return self.woodcut(frame, p.get("levels", 4), p.get("edge_strength", 0.7), ink, paper)
            elif name == "halftone":
                return self.halftone(frame, p.get("halftone_pattern", "dot"), p.get("halftone_frequency", 10.0),
                    p.get("halftone_angle", 45.0), ink, paper)
            elif name == "dither":
                return self.dither(frame, p.get("dither_type", "floyd"), p.get("dither_intensity", 0.8), ink, paper)
            elif name == "engraving":
                return self.engraving(frame, p.get("ink_length", 8), p.get("ink_angle", 45.0), ink, paper)
            elif name == "color_bitmap":
                return self.color_bitmap(frame, p.get("levels", 6), ink, paper)
            elif name == "grayscale_bitmap":
                return self.grayscale_bitmap(frame, p.get("levels", 6), ink, paper)
            elif name == "charcoal":
                return self.charcoal(frame, p.get("ink_density", 0.8), ink, paper)
            elif name in ("none", None, ""):
                return frame
            else:
                log.warning(f"Unknown effect: {name}")
                return frame
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            log.error(f"GPU OOM in effect {name}")
            return frame
        except Exception as e:
            log.error(f"Effect {name} failed: {e}", exc_info=True)
            return frame

    def clear(self):
        self._c.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()