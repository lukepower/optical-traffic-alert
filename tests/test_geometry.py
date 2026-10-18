import math

import pytest

from ota.config import CameraConfig
from ota.geometry import CameraModel, bearing_and_distance, clock_position, vertical_word, wrap180


def test_centre_pixel_is_boresight():
    cam = CameraModel(CameraConfig(width=1920, height=1080, hfov_deg=50))
    d = cam.pixel_to_direction(960, 540)
    assert d.azimuth_deg == pytest.approx(0, abs=1e-9)
    assert d.elevation_deg == pytest.approx(0, abs=1e-9)


def test_image_edge_is_half_fov():
    cam = CameraModel(CameraConfig(width=1920, height=1080, hfov_deg=50))
    assert cam.pixel_to_direction(1920, 540).azimuth_deg == pytest.approx(25, abs=1e-6)
    assert cam.pixel_to_direction(0, 540).azimuth_deg == pytest.approx(-25, abs=1e-6)


def test_mounting_yaw_and_pitch():
    cam = CameraModel(CameraConfig(width=1920, height=1080, hfov_deg=60, yaw_deg=-55, pitch_deg=5))
    d = cam.pixel_to_direction(960, 540)
    assert d.azimuth_deg == pytest.approx(-55, abs=1e-6)
    assert d.elevation_deg == pytest.approx(5, abs=1e-6)


def test_image_up_is_elevation_up():
    cam = CameraModel(CameraConfig(width=1920, height=1080, hfov_deg=50))
    assert cam.pixel_to_direction(960, 100).elevation_deg > 0
    assert cam.pixel_to_direction(960, 1000).elevation_deg < 0


@pytest.mark.parametrize("az,clock", [(0, 12), (14, 12), (16, 1), (30, 1), (90, 3), (180, 6),
                                      (-90, 9), (-30, 11), (-16, 11), (-170, 6)])
def test_clock_position(az, clock):
    assert clock_position(az) == clock


def test_vertical_word():
    assert vertical_word(10) == "high"
    assert vertical_word(-10) == "low"
    assert vertical_word(2) == "level"


def test_deg_per_px_4k_50deg():
    # 4K sensor with 50 deg lens: about 0.015 deg per pixel (see README numbers)
    cam = CameraModel(CameraConfig(width=3840, height=2160, hfov_deg=50))
    assert cam.deg_per_px == pytest.approx(0.0146, rel=0.05)


def test_bearing_and_distance():
    # Bolzano LIPB -> roughly north-east, distance in the right ballpark
    brg, dist = bearing_and_distance(46.4602, 11.3264, 46.5602, 11.3264)
    assert brg == pytest.approx(0, abs=0.01)
    assert dist == pytest.approx(11120, rel=0.01)


def test_wrap180():
    assert wrap180(190) == pytest.approx(-170)
    assert wrap180(-190) == pytest.approx(170)
    assert math.isclose(wrap180(0), 0)
