"""Stage 1: cheap candidate detection with classical image processing.

Idea: a distant aircraft against the sky is a small blob that is darker (or,
with sun glint, brighter) than its immediate surroundings. A morphological
black-hat / top-hat filter removes everything larger than the kernel - sky
gradient, clouds, terrain - and keeps small, contrasty spots.

This runs in real time on a laptop CPU even at 4K and produces a short list of
candidates. Only these small crops go through the neural classifier
(stage 2), which is orders of magnitude cheaper than running a network over
the full frame.

Known limitation: below the horizon (terrain, mountains) there is a lot more
small structure, so the false-candidate rate rises there. The classifier and
the tracker (which demands consistent motion over many frames) handle that.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..config import CameraConfig, DetectorConfig


@dataclass
class Candidate:
    x: float        # centre, full-resolution pixels
    y: float
    w: float
    h: float
    area: float
    contrast: float  # peak response of the morphological filter
    polarity: str    # "dark" or "bright"
    score: float = 1.0  # classifier probability (filled in by stage 2)
    eff_area: float = 0.0  # integrated contrast / peak contrast, in px^2

    @property
    def size(self) -> float:
        """Characteristic size in pixels, used for the growth (tau) estimate.

        sqrt of the effective area if available, else geometric mean of w and h.
        """
        if self.eff_area > 0:
            return float(np.sqrt(self.eff_area))
        return float(np.sqrt(max(self.w, 1.0) * max(self.h, 1.0)))


class CandidateDetector:
    def __init__(self, cfg: DetectorConfig, cam: CameraConfig | None = None):
        self.cfg = cfg
        self.cam = cam
        k = max(3, int(cfg.kernel_px) | 1)  # odd
        shape = cv2.MORPH_ELLIPSE if cfg.kernel_shape == "ellipse" else cv2.MORPH_RECT
        self.kernel = cv2.getStructuringElement(shape, (k, k))
        self._mask_cache: tuple[tuple[int, int], np.ndarray] | None = None
        self._sky: np.ndarray | None = None

    def _mask(self, shape: tuple[int, int]) -> np.ndarray | None:
        if not self.cam or not self.cam.masks:
            return None
        if self._mask_cache and self._mask_cache[0] == shape:
            return self._mask_cache[1]
        h, w = shape
        m = np.full((h, w), 255, np.uint8)
        for r in self.cam.masks:
            m[int(r.y0 * h):int(r.y1 * h), int(r.x0 * w):int(r.x1 * w)] = 0
        self._mask_cache = (shape, m)
        return m

    def sky_mask(self, gray: np.ndarray) -> np.ndarray:
        """Per-frame sky segmentation (255 = sky) at full resolution.

        A heavily blurred thumbnail is split into a bright and a dark class
        with Otsu's method; the bright class is the sky. The mask is eroded so
        the horizon edge itself (a strong, textured edge) is excluded.
        """
        h, w = gray.shape
        f = 8
        thumb = cv2.resize(gray, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
        thumb = cv2.GaussianBlur(thumb, (0, 0), 2)
        _, sky = cv2.threshold(thumb, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        sky = cv2.morphologyEx(sky, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        if self.cfg.sky_connected_top:
            n, lab = cv2.connectedComponents(sky)
            top = set(np.unique(lab[0, :])) - {0}
            if not top and n > 1:  # nothing touches the top (e.g. steep bank): largest region
                top = {int(np.argmax(np.bincount(lab.ravel())[1:])) + 1}
            sky = np.isin(lab, list(top)).astype(np.uint8) * 255
        margin = max(1, int(round(self.cfg.sky_margin_px / f)))
        sky = cv2.erode(sky, np.ones((2 * margin + 1, 2 * margin + 1), np.uint8))
        return cv2.resize(sky, (w, h), interpolation=cv2.INTER_NEAREST)

    def detect(self, image: np.ndarray) -> list[Candidate]:
        cfg = self.cfg
        gray_full = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        self._sky = self.sky_mask(gray_full) if cfg.sky_only else None
        scales = sorted(cfg.pyramid_scales or [1.0], reverse=True)
        k = self.kernel.shape[0]

        per_scale: list[list[Candidate]] = []
        for scale in scales:
            gray = gray_full if scale == 1.0 else cv2.resize(
                gray_full, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            sky = None
            if self._sky is not None:
                sky = self._sky if scale == 1.0 else cv2.resize(
                    self._sky, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            found = self._detect_level(gray, scale, sky)
            fine = scale == scales[0]
            # Each level keeps the object sizes it handles well: the finest level
            # small objects, coarser levels objects too large for the fine kernel.
            lo = 0.0 if fine else 0.8 * k
            hi = k / max(scale, 1e-6) * 0.9 if len(scales) > 1 else float("inf")
            per_scale.append([c for c in found if lo <= max(c.w, c.h) <= hi])

        # Drop fine-level fragments that lie inside a coarse detection
        results: list[Candidate] = []
        for level in reversed(per_scale):  # coarse first
            for c in level:
                inside = any(abs(c.x - o.x) <= o.w / 2 + 2 and abs(c.y - o.y) <= o.h / 2 + 2
                             for o in results)
                if not inside:
                    results.append(c)

        if cfg.border_px > 0:
            h, w = gray_full.shape
            b = cfg.border_px
            results = [c for c in results if b <= c.x < w - b and b <= c.y < h - b]
        results.sort(key=lambda c: c.contrast, reverse=True)
        return results[: cfg.max_candidates]

    def _detect_level(self, gray: np.ndarray, scale: float,
                      sky: np.ndarray | None = None) -> list[Candidate]:
        cfg = self.cfg
        mask = self._mask(gray.shape)
        if sky is not None:
            mask = sky if mask is None else cv2.bitwise_and(mask, sky)
        k = self.kernel.shape[0]
        results: list[Candidate] = []
        ops = []
        if cfg.detect_dark:
            ops.append(("dark", cv2.MORPH_CLOSE))
        if cfg.detect_bright:
            ops.append(("bright", cv2.MORPH_OPEN))

        for polarity, op in ops:
            # background = the image with small objects removed
            background = cv2.morphologyEx(gray, op, self.kernel)
            resp = cv2.subtract(background, gray) if polarity == "dark" else cv2.subtract(gray, background)
            if mask is not None:
                resp = cv2.bitwise_and(resp, resp, mask=mask)
            mean, std = cv2.meanStdDev(resp)
            thr = max(cfg.min_contrast, float(mean[0][0] + cfg.sigma_k * std[0][0]))
            _, binary = cv2.threshold(resp, thr, 255, cv2.THRESH_BINARY)
            # join fragments of one object (fuselage / wings / tail)
            binary = cv2.dilate(binary, np.ones((3, 3), np.uint8))
            half = (3 * k) // 2
            H, W = background.shape[:2]

            for x, y, w, h, area, cx, cy in _components(binary):
                # dilation added a 1 px border; remove it again from the size
                w, h = max(1, w - 2), max(1, h - 2)
                area = max(1, area - 2 * (w + h))
                area_full = area / (scale * scale)
                if area_full < cfg.min_area_px or area_full > cfg.max_area_px:
                    continue
                if cfg.max_background_std > 0:
                    # local uniformity of the background around the candidate
                    ix, iy = int(cx), int(cy)
                    win = background[max(0, iy - half):min(H, iy + half + 1),
                                     max(0, ix - half):min(W, ix + half + 1)]
                    if float(win.std()) > cfg.max_background_std:
                        continue
                roi = resp[y:y + h + 2, x:x + w + 2]
                contrast = float(roi.max())
                # Integrated contrast ("mass") divided by the peak gives an
                # effective area that changes smoothly with distance, unlike
                # the pixel count of the thresholded blob, which jumps.
                mass = float(roi[roi > thr * 0.5].sum())
                eff_area = mass / max(contrast, 1.0) / (scale * scale)
                results.append(Candidate(
                    x=cx / scale, y=cy / scale, w=w / scale, h=h / scale,
                    area=area_full, contrast=contrast, polarity=polarity,
                    eff_area=eff_area,
                ))
        return results


def _components(binary: np.ndarray, cell: int = 64, max_points: int = 50000):
    """Connected components of a sparse binary image.

    A full-frame connectedComponentsWithStats on a 4K image costs ~15 ms even
    when only a handful of pixels are set. Here the set pixels are first
    located on a coarse grid; only the regions that contain something are
    labelled. Yields (x, y, w, h, area, cx, cy) in image coordinates.
    """
    pts = cv2.findNonZero(binary)
    if pts is None:
        return
    if len(pts) > max_points:  # dense image: plain full-frame labelling is fine
        n, _, stats, cents = cv2.connectedComponentsWithStats(binary, connectivity=8)
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            yield int(x), int(y), int(w), int(h), int(a), float(cents[i][0]), float(cents[i][1])
        return
    H, W = binary.shape
    gh, gw = (H + cell - 1) // cell, (W + cell - 1) // cell
    grid = np.zeros((gh, gw), np.uint8)
    p = pts.reshape(-1, 2)
    grid[p[:, 1] // cell, p[:, 0] // cell] = 255
    grid = cv2.dilate(grid, np.ones((3, 3), np.uint8))  # objects across cell borders
    n, _, gstats, _ = cv2.connectedComponentsWithStats(grid, connectivity=8)
    for g in range(1, n):
        gx, gy, gw_, gh_, _ = gstats[g]
        x0, y0 = gx * cell, gy * cell
        x1, y1 = min(W, (gx + gw_) * cell), min(H, (gy + gh_) * cell)
        roi = binary[y0:y1, x0:x1]
        m, _, stats, cents = cv2.connectedComponentsWithStats(roi, connectivity=8)
        for i in range(1, m):
            x, y, w, h, a = stats[i]
            yield (int(x + x0), int(y + y0), int(w), int(h), int(a),
                   float(cents[i][0] + x0), float(cents[i][1] + y0))


def crop(image: np.ndarray, c: Candidate, size: int) -> np.ndarray:
    """Square crop centred on a candidate, padded at image borders."""
    half = size // 2
    h, w = image.shape[:2]
    x0, y0 = int(round(c.x)) - half, int(round(c.y)) - half
    pad = cv2.copyMakeBorder(image, half, half, half, half, cv2.BORDER_REFLECT)
    return pad[y0 + half:y0 + half + size, x0 + half:x0 + half + size]
