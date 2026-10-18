"""Synthetic test video generator.

Creates a short sky scene so the whole pipeline can be tested without any
camera or real footage. The scenario contains:

1. **Collision target** at about 1 o'clock, level: constant bearing, apparent
   size grows like 1/(T - t) -> must trigger ADVISORY then WARNING.
2. **Crossing target** moving left to right high: clear bearing change ->
   at most plain TRAFFIC, never ADVISORY/WARNING.
3. **Windscreen specks**: two fixed dark dots that never move or grow ->
   must be suppressed as static artefacts.
4. Clouds, a sky gradient, sensor noise and textured terrain below the horizon.

Ground truth is written next to the video as JSON so tests can check it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np


def _sky(w: int, h: int, horizon: int, rng: np.random.Generator) -> np.ndarray:
    y = np.linspace(0, 1, horizon, dtype=np.float32)[:, None]
    top = np.array([200, 150, 90], np.float32)      # BGR, deep blue
    bottom = np.array([235, 215, 190], np.float32)  # pale near horizon
    sky = (top * (1 - y[..., None]) + bottom * y[..., None]) * np.ones((1, w, 1), np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[:horizon] = sky
    # clouds: large soft white blobs
    cloud = np.zeros((h, w), np.float32)
    for _ in range(6):
        cx, cy = rng.integers(0, w), rng.integers(int(horizon * 0.3), horizon)
        ax, ay = rng.integers(w // 12, w // 5), rng.integers(h // 30, h // 12)
        cv2.ellipse(cloud, (int(cx), int(cy)), (int(ax), int(ay)), 0, 0, 360, 1.0, -1)
    cloud = cv2.GaussianBlur(cloud, (0, 0), sigmaX=w / 60)
    img[:horizon] = img[:horizon] * (1 - 0.6 * cloud[:horizon, :, None]) + 245 * 0.6 * cloud[:horizon, :, None]
    # terrain: smooth dark-green with low-frequency texture
    terrain = rng.normal(0, 1, (h // 16 + 1, w // 16 + 1)).astype(np.float32)
    terrain = cv2.resize(terrain, (w, h), interpolation=cv2.INTER_CUBIC)
    base = np.array([60, 95, 70], np.float32)
    img[horizon:] = base + 12 * terrain[horizon:, :, None]
    return img


def _draw_aircraft(img: np.ndarray, x: float, y: float, size: float, shade: float = 55) -> None:
    """Very simple aircraft silhouette: fuselage + wing, anti-aliased by supersampling."""
    s = 4  # supersampling
    r = int(max(size, 2) * 1.2) + 4
    x0, y0 = int(x) - r, int(y) - r
    patch = np.zeros(((2 * r) * s, (2 * r) * s), np.float32)
    cx, cy = (x - x0) * s, (y - y0) * s
    span = size * s
    cv2.ellipse(patch, (int(cx), int(cy)), (max(1, int(span * 0.18)), max(1, int(span * 0.22))),
                0, 0, 360, 1.0, -1)
    cv2.line(patch, (int(cx - span / 2), int(cy)), (int(cx + span / 2), int(cy)), 1.0,
             max(1, int(span * 0.08)))
    alpha = cv2.resize(patch, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    xs, ys = max(0, x0), max(0, y0)
    xe, ye = min(w, x0 + 2 * r), min(h, y0 + 2 * r)
    if xe <= xs or ye <= ys:
        return
    a = alpha[ys - y0:ye - y0, xs - x0:xe - x0, None]
    img[ys:ye, xs:xe] = img[ys:ye, xs:xe] * (1 - a) + shade * a


def make_synthetic_video(path: str | Path, width: int = 1920, height: int = 1080, fps: float = 30.0,
                         duration_s: float = 30.0, hfov_deg: float = 50.0, seed: int = 7) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    horizon = int(height * 0.72)
    bg = _sky(width, height, horizon, rng)
    f = (width / 2) / math.tan(math.radians(hfov_deg) / 2)

    def px(az: float, el: float) -> tuple[float, float]:
        return width / 2 + f * math.tan(math.radians(az)), height / 2 - f * math.tan(math.radians(el))

    T_contact = duration_s + 2.0
    s0 = 3.0
    collision_xy = px(20.0, 1.0)
    specks = [(width * 0.33, height * 0.25), (width * 0.62, height * 0.40)]

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    n = int(duration_s * fps)
    for i in range(n):
        t = i / fps
        img = bg.copy()
        # 1. collision course: constant bearing, growing
        size = s0 * T_contact / (T_contact - t)
        jx, jy = rng.normal(0, 0.3, 2)
        _draw_aircraft(img, collision_xy[0] + jx, collision_xy[1] + jy, size)
        # 2. crossing traffic: az from -30 to +10 deg over the clip, el +8 deg
        az = -30 + 40 * t / duration_s
        cx, cy = px(az, 8.0)
        _draw_aircraft(img, cx, cy, 4.0)
        # 3. windscreen specks (static, not growing)
        for sx, sy in specks:
            cv2.circle(img, (int(sx), int(sy)), 2, (70, 70, 70), -1)
        # sensor noise
        img += rng.normal(0, 2.0, img.shape).astype(np.float32)
        writer.write(np.clip(img, 0, 255).astype(np.uint8))
    writer.release()

    truth = {
        "video": path.name, "width": width, "height": height, "fps": fps,
        "duration_s": duration_s, "hfov_deg": hfov_deg,
        "targets": [
            {"name": "collision", "azimuth_deg": 20.0, "elevation_deg": 1.0,
             "expected": "WARNING", "contact_time_s": T_contact,
             "pixel": [round(collision_xy[0], 1), round(collision_xy[1], 1)]},
            {"name": "crossing", "azimuth_deg": [-30.0, 10.0], "elevation_deg": 8.0,
             "expected": "no ADVISORY/WARNING"},
            {"name": "windscreen_specks", "pixels": specks, "expected": "suppressed"},
        ],
    }
    path.with_suffix(".json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth
