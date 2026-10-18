"""Multi-target tracker in image coordinates.

Why tracking matters more than single-frame detection: a distant aircraft is
a 2-5 px dot. Many things look like that in one frame (noise, insects, specks
on the windscreen). Only a dot that is seen consistently over many frames and
moves smoothly is a target. Windscreen specks do not move relative to the
camera at all; noise does not persist.

Implementation
--------------
* one alpha-beta filter (constant velocity) per track, in pixels
* greedy nearest-neighbour association inside a gate
* tentative tracks become confirmed after ``min_hits`` associations
* tracks are dropped after ``max_misses`` frames without a detection

The tracker is deliberately simple and dependency-free. If crossing targets
become a problem, swap in a Hungarian assignment (scipy) - the interface stays
the same.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field

from ..config import TrackerConfig
from ..detection.candidates import Candidate


@dataclass
class TrackPoint:
    t: float
    x: float
    y: float
    size: float
    contrast: float


@dataclass
class Track:
    id: int
    camera: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    hits: int = 1
    misses: int = 0
    confirmed: bool = False
    first_t: float = 0.0
    last_t: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=600))
    # filled in by the threat assessor / fusion
    meta: dict = field(default_factory=dict)

    @property
    def age(self) -> float:
        return self.last_t - self.first_t

    def predict(self, t: float) -> tuple[float, float]:
        dt = t - self.last_t
        return self.x + self.vx * dt, self.y + self.vy * dt


class Tracker:
    def __init__(self, cfg: TrackerConfig, camera: str = "cam"):
        self.cfg = cfg
        self.camera = camera
        self.tracks: list[Track] = []
        self._ids = itertools.count(1)

    def update(self, detections: list[Candidate], t: float) -> list[Track]:
        cfg = self.cfg
        # 1. pair every track with every detection inside the gate
        pairs = []
        for ti, tr in enumerate(self.tracks):
            px, py = tr.predict(t)
            gate = cfg.gate_px * (1.0 + 0.5 * tr.misses)
            for di, d in enumerate(detections):
                dist = math.hypot(d.x - px, d.y - py)
                if dist <= gate:
                    pairs.append((dist, ti, di))
        pairs.sort()

        # 2. greedy assignment, closest first
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            self._correct(self.tracks[ti], detections[di], t)

        # 3. unmatched tracks age, unmatched detections start new tracks
        for ti, tr in enumerate(self.tracks):
            if ti not in used_t:
                tr.misses += 1
        for di, d in enumerate(detections):
            if di not in used_d:
                tr = Track(id=next(self._ids), camera=self.camera, x=d.x, y=d.y,
                           first_t=t, last_t=t)
                tr.history.append(TrackPoint(t, d.x, d.y, d.size, d.contrast))
                self.tracks.append(tr)

        self.tracks = [tr for tr in self.tracks if tr.misses <= cfg.max_misses]
        # history pruning by time
        for tr in self.tracks:
            while tr.history and t - tr.history[0].t > cfg.history_s:
                tr.history.popleft()
        return self.confirmed()

    def _correct(self, tr: Track, d: Candidate, t: float) -> None:
        a, b = self.cfg.alpha, self.cfg.beta
        dt = max(t - tr.last_t, 1e-3)
        px, py = tr.predict(t)
        rx, ry = d.x - px, d.y - py
        tr.x, tr.y = px + a * rx, py + a * ry
        tr.vx += b * rx / dt
        tr.vy += b * ry / dt
        tr.last_t = t
        tr.hits += 1
        tr.misses = 0
        if tr.hits >= self.cfg.min_hits:
            tr.confirmed = True
        tr.history.append(TrackPoint(t, d.x, d.y, d.size, d.contrast))

    def confirmed(self) -> list[Track]:
        return [tr for tr in self.tracks if tr.confirmed and tr.misses == 0]
