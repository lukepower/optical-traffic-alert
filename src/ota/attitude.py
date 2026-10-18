"""Camera attitude from an IMU on the camera, aided by GPS (offline, for the evaluation).

Why not just use the IMU's own roll/pitch? A gravity-referenced AHRS assumes the
accelerometer sees only gravity. In a coordinated turn the apparent gravity
points through the cockpit floor, so such an AHRS slowly "levels" while the
aircraft is banked. Aircraft AHRS fix this with GPS: the centripetal
acceleration is known from the GPS velocity, so the true direction of gravity
can be separated from it.

This module runs a Mahony-type complementary filter over the logged raw gyro
and accelerometer data:

* gyro integration (fast, drifts) ...
* ... corrected towards the measured specific force, compared with the
  *expected* specific force ``a_gps - g`` (GPS-aided, correct in turns) and
* ... corrected in heading towards the GPS track (when moving > 15 m/s).
  A constant crab angle remains; the evaluation's heading correction absorbs it.

Frames (all right-handed):

* navigation frame ENU: x East, y North, z Up
* body frame FLU = IMU axes (WitMotion: X forward, Y left, Z up), mounted on the
  camera with X along the optical axis and Z up in the image
* attitude ``R`` maps body vectors to ENU; heading clockwise from north,
  pitch nose-up positive, roll right-wing-down positive.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

G = 9.80665
_R0 = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])  # FLU (level, north) -> ENU


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rotation(heading_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Body (FLU) -> ENU for a heading (cw from north), pitch (nose up) and roll (right down)."""
    return (_rz(-math.radians(heading_deg)) @ _R0 @ _ry(-math.radians(pitch_deg))
            @ _rx(math.radians(roll_deg)))


def euler(R: np.ndarray) -> tuple[float, float, float]:
    """Inverse of ``rotation``: (heading, pitch, roll) in degrees."""
    x, y, z = R[:, 0], R[:, 1], R[:, 2]
    heading = math.degrees(math.atan2(x[0], x[1])) % 360.0
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, x[2]))))
    roll = math.degrees(math.atan2(y[2], z[2]))
    return heading, pitch, roll


def body_angles(R: np.ndarray, d_enu: np.ndarray) -> tuple[float, float]:
    """Direction (ENU) -> (azimuth right-positive, elevation up-positive) in the body frame, degrees."""
    d = R.T @ d_enu
    return (math.degrees(math.atan2(-d[1], d[0])),
            math.degrees(math.atan2(d[2], math.hypot(d[0], d[1]))))


def _skew(w):
    return np.array([[0, -w[2], w[1]], [w[2], 0, -w[0]], [-w[1], w[0], 0]])


def _expm(w):
    th = float(np.linalg.norm(w))
    if th < 1e-12:
        return np.eye(3) + _skew(w)
    k = _skew(w / th)
    return np.eye(3) + math.sin(th) * k + (1 - math.cos(th)) * (k @ k)


def load_imu_csv(path: Path) -> np.ndarray:
    """Rows: t, ax, ay, az (g), gx, gy, gz (deg/s)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([float(r[k]) for k in ("t_wall", "ax", "ay", "az", "gx", "gy", "gz")])
    a = np.array(rows, dtype=np.float64) if rows else np.zeros((0, 7))
    return a[np.argsort(a[:, 0], kind="stable")] if len(a) else a


def smooth_times(t: np.ndarray, block: int = 2000) -> np.ndarray:
    """Serial data arrives in bursts; fit a straight line per block of samples (rate is constant)."""
    out = t.copy()
    n = len(t)
    for s in range(0, n, block):
        e = min(n, s + block)
        if e - s < 10:
            continue
        idx = np.arange(s, e)
        k, d = np.polyfit(idx, t[s:e], 1)
        fit = k * idx + d
        # a burst only delays samples: shift the line so it is not later than any sample
        fit += min(0.0, float(np.min(t[s:e] - fit)))
        out[s:e] = fit
    return out


@dataclass
class GpsAid:
    """Velocity and acceleration (ENU) from the own GPS reports, interpolated in time."""
    t: np.ndarray
    v: np.ndarray        # (n, 3) m/s
    a: np.ndarray        # (n, 3) m/s^2
    track: np.ndarray    # deg (unwrapped)
    speed: np.ndarray    # m/s horizontal

    @classmethod
    def from_reports(cls, own: list) -> "GpsAid | None":
        rows = [(r.t, r.gs_kt, r.track_deg, r.alt_m) for r in own
                if r.gs_kt is not None and r.track_deg is not None]
        if len(rows) < 3:
            return None
        a = np.array(sorted(rows), dtype=np.float64)
        t = a[:, 0]
        spd = a[:, 1] * 0.514444
        trk = np.degrees(np.unwrap(np.radians(a[:, 2])))
        alt = a[:, 3]
        vu = np.gradient(np.nan_to_num(alt, nan=float(np.nanmean(alt)) if np.isfinite(alt).any() else 0.0), t)
        v = np.stack([spd * np.sin(np.radians(trk)), spd * np.cos(np.radians(trk)), vu], axis=1)
        acc = np.stack([np.gradient(v[:, i], t) for i in range(3)], axis=1)
        return cls(t, v, acc, trk, spd)

    def at(self, t: float):
        if t < self.t[0] - 2 or t > self.t[-1] + 2:
            return None
        a = np.array([np.interp(t, self.t, self.a[:, i]) for i in range(3)])
        trk = float(np.interp(t, self.t, self.track)) % 360.0
        spd = float(np.interp(t, self.t, self.speed))
        return a, trk, spd


class AttitudeTrack:
    """Attitude over time; ``at(t)`` returns R (body -> ENU) or None."""

    def __init__(self, t: np.ndarray, R: np.ndarray, valid: np.ndarray):
        self.t, self.R, self.valid = t, R, valid

    def at(self, t: float, max_gap: float = 0.1):
        if len(self.t) == 0:
            return None
        i = int(np.searchsorted(self.t, t))
        cands = [j for j in (i - 1, i) if 0 <= j < len(self.t)]
        j = min(cands, key=lambda j: abs(self.t[j] - t))
        if abs(self.t[j] - t) > max_gap or not self.valid[j]:
            return None
        return self.R[j]

    def static_pitch_roll(self) -> tuple[float, float] | None:
        """Median pitch/roll (for a fixed ground camera)."""
        if not len(self.R):
            return None
        e = np.array([euler(r)[1:] for r in self.R[self.valid]])
        return (float(np.median(e[:, 0])), float(np.median(e[:, 1]))) if len(e) else None


def estimate_attitude(imu: np.ndarray, gps: GpsAid | None, kp: float = 1.0, ki: float = 0.02,
                      k_heading: float = 0.3, init_heading: float = 0.0) -> AttitudeTrack:
    """GPS-aided complementary filter over logged IMU samples (see module docstring)."""
    n = len(imu)
    if n == 0:
        return AttitudeTrack(np.zeros(0), np.zeros((0, 3, 3)), np.zeros(0, bool))
    t = smooth_times(imu[:, 0])
    acc = imu[:, 1:4] * G
    gyr = np.radians(imu[:, 4:7])
    # initial attitude from the first 0.5 s of accelerometer data
    k0 = max(1, int(np.searchsorted(t, t[0] + 0.5)))
    f = acc[:k0].mean(axis=0)
    pitch0 = math.degrees(math.atan2(f[0], math.hypot(f[1], f[2])))
    roll0 = math.degrees(math.atan2(f[1], f[2]))
    head0 = init_heading
    if gps is not None:
        g0 = gps.at(t[0])
        if g0 is not None and g0[2] > 15:
            head0 = g0[1]
    R = rotation(head0, pitch0, roll0)
    integ = np.zeros(3)
    out = np.zeros((n, 3, 3))
    valid = np.zeros(n, bool)
    heading_locked = gps is None
    up = np.array([0.0, 0.0, 1.0])
    for k in range(n):
        dt = t[k] - t[k - 1] if k else 0.0
        if dt <= 0 or dt > 0.5:
            dt = 0.0
        fb = acc[k]
        e = np.zeros(3)
        aid = gps.at(t[k]) if gps is not None else None
        fn = (aid[0] if aid is not None else np.zeros(3)) + G * up
        nf = np.linalg.norm(fb)
        if 0.5 * G < nf < 2.5 * G:
            vb = R.T @ fn
            e += np.cross(fb / nf, vb / np.linalg.norm(vb))
        if aid is not None and aid[2] > 15:
            hd = euler(R)[0]
            eh = math.radians((aid[1] - hd + 180) % 360 - 180)
            if not heading_locked and abs(eh) > math.radians(20):
                R = rotation(aid[1], *euler(R)[1:])  # first fix: jump to the GPS track
                eh = 0.0
            heading_locked = True
            e += k_heading * (R.T @ (-eh * up))
        integ += ki * e * dt
        w = gyr[k] + kp * e + integ
        R = R @ _expm(w * dt)
        if k % 50 == 0:
            u, _, vt = np.linalg.svd(R)
            R = u @ vt
        out[k] = R
        valid[k] = heading_locked and k > k0
    return AttitudeTrack(t, out, valid)
