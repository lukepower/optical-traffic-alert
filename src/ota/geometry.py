"""Camera geometry: pixels -> direction relative to the aircraft -> callout words.

Coordinate conventions (aircraft body frame):

* x = right wing, y = up, z = forward (nose)
* azimuth: 0 deg = straight ahead, +90 = right wing, -90 = left wing
* elevation: + = above the aircraft's longitudinal plane

A simple pinhole model is used. Lens distortion is ignored for now; for the
narrow lenses planned (40-60 deg) the error is small compared with the
resolution of a clock position (30 deg). See docs/calibration.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import CameraConfig


def _rot_x(a: float) -> np.ndarray:  # pitch, about the right axis
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_y(a: float) -> np.ndarray:  # yaw, about the up axis (+ = to the right)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rot_z(a: float) -> np.ndarray:  # roll, about the forward axis
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


@dataclass
class Direction:
    azimuth_deg: float
    elevation_deg: float


class CameraModel:
    """Maps image pixels of one camera to azimuth/elevation in the body frame."""

    def __init__(self, cam: CameraConfig, width: int | None = None, height: int | None = None):
        self.width = width or cam.width
        self.height = height or cam.height
        self.cx = self.width / 2.0
        self.cy = self.height / 2.0
        self.f = (self.width / 2.0) / math.tan(math.radians(cam.hfov_deg) / 2.0)
        # camera -> body rotation. Order: roll about the optical axis, then pitch, then yaw.
        self.R = (_rot_y(math.radians(cam.yaw_deg))
                  @ _rot_x(-math.radians(cam.pitch_deg))
                  @ _rot_z(-math.radians(cam.roll_deg)))

    @property
    def deg_per_px(self) -> float:
        return math.degrees(math.atan(1.0 / self.f))

    def pixel_to_direction(self, u: float, v: float) -> Direction:
        # camera frame: x right, y up (image v grows downwards), z forward
        d = np.array([(u - self.cx) / self.f, -(v - self.cy) / self.f, 1.0])
        b = self.R @ d
        az = math.degrees(math.atan2(b[0], b[2]))
        el = math.degrees(math.atan2(b[1], math.hypot(b[0], b[2])))
        return Direction(az, el)

    def angular_size_deg(self, size_px: float) -> float:
        return size_px * self.deg_per_px


def clock_position(azimuth_deg: float) -> int:
    """Azimuth -> clock position 1..12 (12 = ahead, 3 = right, 9 = left)."""
    hour = round((azimuth_deg % 360.0) / 30.0) % 12
    return 12 if hour == 0 else hour


def vertical_word(elevation_deg: float, band_deg: float = 5.0) -> str:
    if elevation_deg > band_deg:
        return "high"
    if elevation_deg < -band_deg:
        return "low"
    return "level"


def bearing_and_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Initial true bearing (deg) and great-circle distance (m) from point 1 to point 2."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist = 2 * r * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    brg = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return brg, dist


def wrap180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0
