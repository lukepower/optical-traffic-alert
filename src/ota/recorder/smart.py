"""Smart recording: keep full frame rate only when something is going on.

4K at 15 fps is 1-2 GB per minute, and most of it is empty sky. In *smart*
mode the recorder

* writes the video in short **chunks** (default 10 s) and decides afterwards
  whether to keep or delete each chunk,
* keeps a chunk if a **trigger** was active from ``pre_s`` seconds after its end
  back to ``post_s`` seconds before its start (so every event is recorded with
  lead-in and run-out, without holding 4K frames in memory),
* always writes an **idle stream**: one frame every ``idle_interval_s`` seconds,
  kept regardless. It is cheap and provides "nothing here" material
  (clouds, birds, reflections) for training against false alarms.

Triggers:

1. **Traffic** - a cooperative aircraft (ADS-B/FLARM/OGN) within
   ``trigger_radius_km`` and inside the camera's field of view plus a margin.
2. **Motion** - the candidate detector runs on the newest frame about twice a
   second; a track that moves for a while triggers. This catches
   non-cooperative traffic (gliders without FLARM, paragliders, drones) and
   birds - exactly the material that the traffic data cannot provide.
"""

from __future__ import annotations

import bisect
import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from ..config import CameraConfig, DetectorConfig, TrackerConfig
from ..sensors.traffic import TrafficTable, look_angles


class TriggerLog:
    """Thread-safe list of times at which a trigger was active, with reasons."""

    def __init__(self):
        self._lock = threading.Lock()
        self._times: list[float] = []
        self.counts: dict[str, int] = {}
        self.last: dict[str, float] = {}

    def add(self, t: float, reason: str) -> None:
        with self._lock:
            bisect.insort(self._times, t)
            self.counts[reason] = self.counts.get(reason, 0) + 1
            self.last[reason] = max(t, self.last.get(reason, 0.0))

    def any_between(self, a: float, b: float) -> bool:
        with self._lock:
            i = bisect.bisect_left(self._times, a)
            return i < len(self._times) and self._times[i] <= b

    def active(self, now: float, within_s: float = 2.0) -> list[str]:
        with self._lock:
            return sorted(r for r, t in self.last.items() if now - t <= within_s)


def traffic_in_view(table: TrafficTable, observer: tuple[float, float, float],
                    heading_deg: float | None, pitch_deg: float, hfov_deg: float, aspect: float,
                    radius_km: float, margin_deg: float = 10.0, max_age_s: float = 8.0,
                    now: float | None = None, exclude: set[str] | None = None,
                    min_range_m: float = 300.0) -> list[str]:
    """Keys of cooperative aircraft near the camera's field of view.

    ``heading_deg`` None (own heading unknown in the aircraft): only the radius
    counts. Targets closer than ``min_range_m`` are the own aircraft's own
    transponder/FLARM and are ignored, as are addresses in ``exclude``."""
    now = time.time() if now is None else now
    vfov = math.degrees(2 * math.atan(math.tan(math.radians(hfov_deg) / 2) / aspect))
    hits = []
    exclude = exclude or set()
    for r in table.active(max_age_s, now):
        if r.addr in exclude:
            continue
        alt = r.alt_m if r.alt_m is not None else observer[2]
        az, el, rng = look_angles(*observer, r.lat, r.lon, alt)
        if rng > radius_km * 1000 or rng < min_range_m:
            continue
        if heading_deg is None:
            hits.append(r.key)
            continue
        if abs((az - heading_deg + 180) % 360 - 180) > hfov_deg / 2 + margin_deg:
            continue
        if abs(el - pitch_deg) > vfov / 2 + margin_deg:
            continue
        hits.append(r.key)
    return hits


@dataclass
class _MotionTrack:
    points: list = field(default_factory=list)  # (t, x, y)


class MotionTrigger:
    """Runs the candidate detector + a tracker on the newest frame ~2x per second.

    A confirmed track that has moved by ``min_move_px`` over at least
    ``min_age_s`` counts as motion. Static things (windscreen specks, dead
    pixels) never move and never trigger."""

    def __init__(self, triggers: TriggerLog, width: int, height: int, rate_hz: float = 2.0,
                 min_move_px: float = 6.0, min_age_s: float = 1.5):
        from ..detection import CandidateDetector
        from ..tracking import Tracker
        self.triggers = triggers
        self.period = 1.0 / rate_hz
        self.min_move_px = min_move_px
        self.min_age_s = min_age_s
        cam = CameraConfig(name="motion", width=width, height=height)
        # full resolution only: the coarse level is for close targets, which also
        # move a lot at full resolution
        self.detector = CandidateDetector(DetectorConfig(pyramid_scales=[1.0]), cam)
        # low rate -> larger gate, fewer hits needed
        self.tracker = Tracker(TrackerConfig(gate_px=150, min_hits=3, max_misses=4), "motion")
        self._lock = threading.Lock()
        self._frame: tuple[float, np.ndarray] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.busy_ms = 0.0
        self.moving_tracks = 0

    def offer(self, t_wall: float, img: np.ndarray) -> None:
        """Called by the capture thread for every frame (cheap: keeps a reference)."""
        with self._lock:
            self._frame = (t_wall, img)

    def start(self) -> "MotionTrigger":
        self._thread = threading.Thread(target=self._run, name="motion", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def process(self, t_wall: float, img: np.ndarray) -> bool:
        cands = self.detector.detect(img)
        tracks = self.tracker.update(cands, t_wall)
        moving = 0
        for tr in tracks:
            h = tr.history
            if len(h) < 2 or h[-1].t - h[0].t < self.min_age_s:
                continue
            if math.hypot(h[-1].x - h[0].x, h[-1].y - h[0].y) >= self.min_move_px:
                moving += 1
        self.moving_tracks = moving
        if moving:
            self.triggers.add(t_wall, "motion")
        return moving > 0

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            with self._lock:
                item, self._frame = self._frame, None
            if item is not None:
                self.process(*item)
                self.busy_ms = (time.perf_counter() - t0) * 1000
            self._stop.wait(max(0.05, self.period - (time.perf_counter() - t0)))
