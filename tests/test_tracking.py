import numpy as np

from ota.config import CameraConfig, ThreatConfig, TrackerConfig
from ota.detection.candidates import Candidate
from ota.geometry import CameraModel
from ota.tracking import ThreatAssessor, ThreatLevel, Tracker
from ota.tracking.threat import robust_trend

FPS = 30.0
CAM = CameraConfig(width=1920, height=1080, hfov_deg=50)


def _cand(x, y, size):
    return Candidate(x=x, y=y, w=size, h=size, area=size * size, contrast=50,
                     polarity="dark", eff_area=size * size)


def _run(positions, sizes, seconds):
    """Feed one synthetic target to tracker + assessor, return assessments over time."""
    tracker = Tracker(TrackerConfig())
    assessor = ThreatAssessor(ThreatConfig(), CameraModel(CAM))
    rng = np.random.default_rng(0)
    out = []
    for i in range(int(seconds * FPS)):
        t = i / FPS
        x, y = positions(t)
        s = sizes(t)
        det = [_cand(x + rng.normal(0, 0.2), y + rng.normal(0, 0.2), s + rng.normal(0, 0.1))]
        for tr in tracker.update(det, t):
            out.append((t, assessor.assess(tr)))
    return out


def test_tracker_confirms_after_min_hits():
    tracker = Tracker(TrackerConfig(min_hits=5))
    for i in range(4):
        assert tracker.update([_cand(100 + i, 100, 3)], i / FPS) == []
    assert len(tracker.update([_cand(104, 100, 3)], 4 / FPS)) == 1


def test_tracker_drops_lost_tracks():
    tracker = Tracker(TrackerConfig(min_hits=2, max_misses=3))
    for i in range(5):
        tracker.update([_cand(100, 100, 3)], i / FPS)
    for i in range(5, 10):
        tracker.update([], i / FPS)
    assert tracker.tracks == []


def test_collision_course_raises_warning():
    contact = 25.0
    res = _run(lambda t: (1500, 500), lambda t: 3 * contact / (contact - t), 20)
    levels = [a.level for _, a in res]
    assert ThreatLevel.WARNING in levels
    first_warning = next(t for t, a in res if a.level == ThreatLevel.WARNING)
    # must warn at least 5 s before contact
    assert contact - first_warning >= 5


def test_crossing_target_is_only_traffic():
    # moves 1 deg/s across the image, constant size
    res = _run(lambda t: (400 + 40 * t, 300), lambda t: 4.0, 15)
    levels = {a.level for _, a in res}
    assert ThreatLevel.ADVISORY not in levels and ThreatLevel.WARNING not in levels
    assert ThreatLevel.TRAFFIC in levels


def test_static_speck_is_suppressed():
    res = _run(lambda t: (800, 300), lambda t: 3.0, 10)
    assert all(a.level == ThreatLevel.NONE for _, a in res)


def test_robust_trend():
    t = np.linspace(0, 5, 100)
    slope, k = robust_trend(t, 2 + 0.5 * t)
    assert abs(slope - 0.5) < 1e-6 and k > 0.99
    rng = np.random.default_rng(1)
    _, k_noise = robust_trend(t, 5 + rng.normal(0, 1, t.size))
    assert abs(k_noise) < 0.2
