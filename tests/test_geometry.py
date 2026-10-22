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


def test_direction_to_pixel_roundtrip():
    from ota.config import CameraConfig
    from ota.geometry import CameraModel
    cam = CameraConfig(width=1280, height=720, hfov_deg=50, yaw_deg=190, pitch_deg=3, roll_deg=2)
    m = CameraModel(cam)
    for u, v in [(640, 360), (10, 20), (1200, 700), (300, 500)]:
        d = m.pixel_to_direction(u, v)
        uu, vv = m.direction_to_pixel(d.azimuth_deg, d.elevation_deg)
        assert abs(uu - u) < 1e-6 and abs(vv - v) < 1e-6
    assert m.direction_to_pixel(10, 0) is None  # behind the camera


def test_sky_mask_ignores_bright_patches_below_horizon_and_border():
    import cv2
    import numpy as np
    from ota.config import DetectorConfig
    from ota.detection.candidates import CandidateDetector
    img = np.full((720, 1280), 200, np.uint8)          # sky
    img[400:] = 60                                     # terrain
    img[550:650, 300:500] = 230                        # white wall below the horizon
    for x, y in ((400, 600), (900, 200), (5, 200)):     # dot on the wall, in the sky, at the border
        cv2.circle(img, (x, y), 2, 20, -1)
    det = CandidateDetector(DetectorConfig(pyramid_scales=[1.0]))
    sky = det.sky_mask(img)
    assert sky[100, 640] == 255 and sky[600, 400] == 0
    found = {(round(c.x / 100), round(c.y / 100)) for c in det.detect(img)}
    assert (9, 2) in found and (4, 6) not in found and (0, 2) not in found
