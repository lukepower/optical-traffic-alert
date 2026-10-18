import math

import numpy as np
import pytest

from ota.attitude import (G, AttitudeTrack, GpsAid, body_angles, estimate_attitude, euler,
                          rotation, smooth_times)
from ota.sensors.traffic import TrafficReport
from ota.sensors.witmotion import ImuSample, WitMotionParser, encode_sample


def test_rotation_euler_roundtrip_and_conventions():
    for h, p, r in [(0, 0, 0), (90, 5, -10), (271, -3, 30), (10, 20, 45)]:
        hh, pp, rr = euler(rotation(h, p, r))
        assert (hh, pp, rr) == pytest.approx((h % 360, p, r), abs=1e-9)
    R = rotation(90, 0, 0)                   # flying east
    assert R @ np.array([1, 0, 0]) == pytest.approx([1, 0, 0])   # forward = east
    az, el = body_angles(R, np.array([0.0, -1.0, 0.1]))        # target to the south = right wing
    assert az == pytest.approx(90, abs=1e-6) and el > 0
    # right bank: the right wing points down -> a target straight below-right appears 'level'
    Rb = rotation(0, 0, 30)
    az, el = body_angles(Rb, np.array([math.cos(math.radians(30)), 0, -math.sin(math.radians(30))]))
    assert el == pytest.approx(0, abs=1e-6) and az == pytest.approx(90, abs=1e-6)


def test_witmotion_parser_roundtrip_and_resync():
    s = ImuSample(0, 0.01, -0.02, 0.99, 1.5, -2.0, 30.0, 3.0, -4.0, 120.0)
    data = b"\x00\x12" + encode_sample(s) + encode_sample(s)
    corrupt = bytearray(encode_sample(s))
    corrupt[5] ^= 0xFF
    p = WitMotionParser()
    out = p.feed(data[:7], 1.0) + p.feed(data[7:] + bytes(corrupt) + encode_sample(s), 2.0)
    assert len(out) == 4 and p.bad == 1   # corrupt acc packet skipped, its angle packet still counts
    o = out[-1]
    assert (o.ax, o.az, o.gz, o.yaw) == pytest.approx((0.01, 0.99, 30.0, 120.0), abs=0.07)  # int16 quantisation


def test_smooth_times_removes_bursts():
    true = 100 + np.arange(4000) * 0.005
    rx = true + 0.03 * (np.arange(4000) % 8 == 0)    # bursts arrive late
    rx[::8] += 0.0
    s = smooth_times(rx)
    assert np.max(np.abs(np.diff(s) - 0.005)) < 1e-4


def simulate_turn(bank_deg=25.0, v=50.0, rate_hz=100.0, dur=60.0, bias_dps=(0.3, -0.2, 0.4), seed=1):
    """Level flight 20 s, coordinated turn, level again. Returns imu array, gps reports, truth fn."""
    rng = np.random.default_rng(seed)
    omega_turn = G * math.tan(math.radians(bank_deg)) / v   # rad/s

    def state(t):
        if t < 20:
            return 0.0, 0.0, 0.0
        if t < 40:
            return math.degrees(omega_turn * (t - 20)), bank_deg, omega_turn
        return math.degrees(omega_turn * 20), 0.0, 0.0

    def truth(t):
        h, b, _ = state(t)
        return rotation(h, 0.0, b)

    ts = np.arange(0, dur, 1 / rate_hz)
    rows = []
    for t in ts:
        R = truth(t)
        dt = 1e-3
        dR = (truth(t + dt) - truth(t - dt)) / (2 * dt)
        W = R.T @ dR
        w_b = np.array([W[2, 1], W[0, 2], W[1, 0]])
        h, b, om = state(t)
        # centripetal acceleration towards the turn centre (to the right of the track)
        a_n = np.zeros(3)
        if om:
            hr = math.radians(h)
            a_n = v * om * np.array([math.cos(hr), -math.sin(hr), 0.0])
        f_b = R.T @ (a_n + np.array([0, 0, G])) / G
        g = np.degrees(w_b) + np.array(bias_dps) + rng.normal(0, 0.05, 3)
        rows.append([1000 + t, *(f_b + rng.normal(0, 0.003, 3)), *g])
    gps = []
    for t in np.arange(0, dur, 1.0):
        h, _, _ = state(t)
        gps.append(TrafficReport(src="own", addr="OWN", t=1000 + t, rx=1000 + t, lat=46, lon=11,
                                 alt_m=1500, track_deg=h % 360, gs_kt=v / 0.514444))
    return np.array(rows), gps, truth


def test_gps_aided_attitude_holds_bank_in_turn():
    imu, gps, truth = simulate_turn()
    att = estimate_attitude(imu, GpsAid.from_reports(gps))
    errs = []
    for t in (25, 30, 35, 38, 50, 55):
        R = att.at(1000 + t)
        h, p, r = euler(R)
        th, tp, tr = euler(truth(t))
        errs.append((abs((h - th + 180) % 360 - 180), abs(p - tp), abs(r - tr)))
    errs = np.array(errs)
    assert errs[:, 2].max() < 2.0    # roll within 2 deg also deep in the turn
    assert errs[:, 1].max() < 2.0
    assert errs[:, 0].max() < 3.0
    # without GPS the gravity-referenced filter "levels" during the sustained turn
    blind = estimate_attitude(imu, None, init_heading=0.0)
    assert abs(euler(blind.at(1000 + 38))[2] - 25) > 5


def test_static_pitch_roll_for_ground_camera():
    R = rotation(0, 4.0, -1.5)
    f = R.T @ np.array([0, 0, 1.0])
    imu = np.array([[100 + k * 0.01, *f, 0.0, 0.0, 0.0] for k in range(500)])
    att = estimate_attitude(imu, None)
    att.valid[:] = True
    p, r = att.static_pitch_roll()
    assert p == pytest.approx(4.0, abs=0.05) and r == pytest.approx(-1.5, abs=0.05)
    assert isinstance(att, AttitudeTrack)
