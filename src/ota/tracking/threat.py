"""Threat assessment: is a tracked target on a collision course?

The classic rule every pilot learns: a target that keeps a **constant bearing**
while **getting bigger** is on a collision course. Exactly that is hard to see
for the human eye (no relative motion), and easy for a camera.

For every confirmed track we compute over a sliding window (least squares):

* bearing rate  - change of azimuth/elevation in deg/s. Own rotation (IMU
  yaw/pitch rates) is subtracted, otherwise every turn would look like
  target motion.
* growth rate   - relative change of apparent size, (ds/dt) / s, in 1/s
* tau           - time to contact estimate = 1 / growth rate (s)

tau from apparent size is noisy for 2-3 px targets; it improves quickly as the
target grows. That is fine: the alert gets more confident as the danger grows.

Levels
------
NONE      - too young, or static (no motion, no growth: windscreen speck, dead pixel)
TRAFFIC   - confirmed moving target, no collision geometry (information only)
ADVISORY  - constant bearing and closing, tau < advisory_tau_s
WARNING   - constant bearing and closing, tau < warning_tau_s
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass

import numpy as np

from ..config import ThreatConfig
from ..geometry import CameraModel, clock_position, vertical_word, wrap180
from .tracker import Track


class ThreatLevel(enum.IntEnum):
    NONE = 0
    TRAFFIC = 1
    ADVISORY = 2
    WARNING = 3


@dataclass
class Assessment:
    track_id: int
    camera: str
    level: ThreatLevel
    azimuth_deg: float
    elevation_deg: float
    clock: int
    vertical: str
    bearing_rate_dps: float
    growth_rate: float
    tau_s: float | None
    size_px: float

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["level"] = self.level.name
        return d


def _slope(t: np.ndarray, y: np.ndarray) -> float:
    return _fit(t, y)[0]


def _fit(t: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Least-squares slope and its t-statistic (slope / standard error)."""
    n = len(t)
    if n < 3 or float(np.ptp(t)) < 1e-6:
        return 0.0, 0.0
    x = t - t.mean()
    sxx = float((x * x).sum())
    slope = float((x * (y - y.mean())).sum() / sxx)
    resid = y - (y.mean() + slope * x)
    se = math.sqrt(max(float((resid * resid).sum()) / (n - 2), 1e-12) / sxx)
    return slope, slope / se


def robust_trend(t: np.ndarray, y: np.ndarray, max_points: int = 60) -> tuple[float, float]:
    """Theil-Sen slope and Kendall rank correlation of y over t.

    Both are robust against outliers and against measurements that jump
    between a few discrete values (typical for blob sizes of 2-5 px).
    Kendall's tau is +1 for a perfectly increasing series, ~0 for noise.
    """
    n = len(t)
    if n < 5 or float(np.ptp(t)) < 1e-6:
        return 0.0, 0.0
    if n > max_points:
        idx = np.linspace(0, n - 1, max_points).astype(int)
        t, y = t[idx], y[idx]
    dt = t[None, :] - t[:, None]
    dy = y[None, :] - y[:, None]
    upper = np.triu(np.ones_like(dt, dtype=bool), 1) & (dt > 0)
    slopes = dy[upper] / dt[upper]
    slope = float(np.median(slopes)) if slopes.size else 0.0
    signs = np.sign(dy[upper])
    kendall = float(signs.mean()) if signs.size else 0.0
    return slope, kendall


class ThreatAssessor:
    def __init__(self, cfg: ThreatConfig, camera: CameraModel, window_s: float = 3.0,
                 growth_window_s: float = 5.0):
        self.cfg = cfg
        self.camera = camera
        self.window_s = window_s
        # Size changes are tiny for distant targets: use a longer window for growth.
        self.growth_window_s = growth_window_s

    def assess(self, track: Track, ego_rates_dps: tuple[float, float] = (0.0, 0.0)) -> Assessment:
        """``ego_rates_dps`` = (yaw rate, pitch rate) of the own aircraft from the IMU."""
        cfg = self.cfg
        pts = [p for p in track.history if track.last_t - p.t <= self.window_s]
        t = np.array([p.t for p in pts])
        dirs = [self.camera.pixel_to_direction(p.x, p.y) for p in pts]
        az = np.unwrap(np.radians([d.azimuth_deg for d in dirs]))
        el = np.radians([d.elevation_deg for d in dirs])
        gpts = [p for p in track.history if track.last_t - p.t <= self.growth_window_s]
        gt = np.array([p.t for p in gpts])
        size = np.array([p.size for p in gpts])

        # Own rotation moves everything in the image; remove it.
        az_rate = math.degrees(_slope(t, az)) + ego_rates_dps[0]
        el_rate = math.degrees(_slope(t, el)) + ego_rates_dps[1]
        bearing_rate = math.hypot(az_rate, el_rate)

        # Apparent size: relative growth rate, accepted only if the trend is
        # consistent (Kendall rank correlation) AND large in absolute pixels.
        # Without this, noisy blob sizes of static clutter produce bogus tau values.
        mean_size = float(size.mean()) if len(size) else 0.0
        ds_dt, trend = robust_trend(gt, size)
        growth = ds_dt / mean_size if mean_size > 0 else 0.0
        span = float(np.ptp(gt)) if len(gt) else 0.0
        significant = trend >= cfg.min_growth_trend and ds_dt * span >= cfg.min_size_change_px
        tau = 1.0 / growth if (growth > cfg.min_growth_rate and significant) else None

        last = dirs[-1]
        azimuth = wrap180(last.azimuth_deg)
        level = ThreatLevel.NONE
        if tau is not None:
            track.meta["ever_closing"] = True
        static = (bearing_rate < cfg.static_max_rate_dps and tau is None
                  and not track.meta.get("ever_closing"))
        track.meta["static"] = static
        if track.age >= cfg.min_track_age_s and not static:
            level = ThreatLevel.TRAFFIC if cfg.announce_all_traffic else ThreatLevel.NONE
            constant_bearing = bearing_rate <= cfg.max_bearing_rate_dps
            raw = ThreatLevel.TRAFFIC
            if constant_bearing and tau is not None:
                if tau <= cfg.warning_tau_s:
                    raw = ThreatLevel.WARNING
                elif tau <= cfg.advisory_tau_s:
                    raw = ThreatLevel.ADVISORY
            # Hysteresis: a collision geometry must persist for threat_hold_s
            # before it is reported. Short statistical flukes never get through.
            since = track.meta.get("threat_since")
            if raw >= ThreatLevel.ADVISORY:
                if since is None:
                    since = track.meta["threat_since"] = track.last_t
                if track.last_t - since >= cfg.threat_hold_s:
                    level = raw
            else:
                track.meta.pop("threat_since", None)
            # Latch: a reported threat is held for threat_release_s, so a few
            # noisy frames (tau briefly not significant) don't make it flicker.
            latched = track.meta.get("latched")
            if level >= ThreatLevel.ADVISORY:
                track.meta["latched"] = (level, track.last_t)
            elif latched and track.last_t - latched[1] <= cfg.threat_release_s \
                    and bearing_rate <= cfg.max_bearing_rate_dps:
                level = latched[0]

        return Assessment(
            track_id=track.id, camera=track.camera, level=level,
            azimuth_deg=azimuth, elevation_deg=last.elevation_deg,
            clock=clock_position(azimuth),
            vertical=vertical_word(last.elevation_deg, cfg.level_band_deg),
            bearing_rate_dps=bearing_rate, growth_rate=growth, tau_s=tau,
            size_px=float(size[-1]) if len(size) else 0.0,
        )
