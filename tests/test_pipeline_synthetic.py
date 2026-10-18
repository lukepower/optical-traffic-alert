"""End-to-end test on a short synthetic video (generated on the fly)."""

import pytest

from ota.config import load_config
from ota.pipeline import Runner
from ota.synth import make_synthetic_video
from ota.tracking import ThreatLevel


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    video = d / "test.mp4"
    # small and short to keep the test fast; collision target reaches tau < 20 s
    make_synthetic_video(video, width=1280, height=720, duration_s=24, fps=20)
    cfg = load_config("config/default.yaml", overrides={
        "output": {"event_log": "", "show": False}, "audio": {"backend": "print"}})
    cfg.cameras[0].source = f"file:{video}"
    runner = Runner(cfg)
    res = runner.run()
    return res, runner


def test_collision_target_warns(results):
    res, runner = results
    warnings = [a for r in res for a in r.assessments if a.level == ThreatLevel.WARNING]
    assert warnings, "collision target was not detected as WARNING"
    # the collision target is at about 20 deg right, level -> 1 o'clock
    assert {a.clock for a in warnings} == {1}
    assert any("closing" in s for s in runner.announcer.spoken)


def test_no_false_warnings_elsewhere(results):
    res, _ = results
    for r in res:
        for a in r.assessments:
            if a.level >= ThreatLevel.ADVISORY:
                assert abs(a.azimuth_deg - 20) < 3, a
