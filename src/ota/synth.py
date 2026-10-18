"""Synthetic test video generator.

Creates a short sky scene so the whole pipeline can be tested without any
camera or real footage. The scenario contains:

1. **Collision target** at about 1 o'clock, level: constant bearing, apparent
   size grows like 1/(T - t) -> must trigger ADVISORY then WARNING.
2. **Crossing target** moving left to right high: clear bearing change ->
   at most plain TRAFFIC, never ADVISORY/WARNING.
3. **Windscreen specks**: two fixed dark dots that never move or grow ->
   must be suppressed as static artefacts.
4. Clouds, a sky gradient, sensor noise and textured terrain below the horizon.

Ground truth is written next to the video as JSON so tests can check it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np


def _sky(w: int, h: int, horizon: int, rng: np.random.Generator) -> np.ndarray:
    y = np.linspace(0, 1, horizon, dtype=np.float32)[:, None]
    top = np.array([200, 150, 90], np.float32)      # BGR, deep blue
    bottom = np.array([235, 215, 190], np.float32)  # pale near horizon
    sky = (top * (1 - y[..., None]) + bottom * y[..., None]) * np.ones((1, w, 1), np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[:horizon] = sky
    # clouds: large soft white blobs
    cloud = np.zeros((h, w), np.float32)
    for _ in range(6):
        cx, cy = rng.integers(0, w), rng.integers(int(horizon * 0.3), horizon)
        ax, ay = rng.integers(w // 12, w // 5), rng.integers(h // 30, h // 12)
        cv2.ellipse(cloud, (int(cx), int(cy)), (int(ax), int(ay)), 0, 0, 360, 1.0, -1)
    cloud = cv2.GaussianBlur(cloud, (0, 0), sigmaX=w / 60)
    img[:horizon] = img[:horizon] * (1 - 0.6 * cloud[:horizon, :, None]) + 245 * 0.6 * cloud[:horizon, :, None]
    # terrain: smooth dark-green with low-frequency texture
    terrain = rng.normal(0, 1, (h // 16 + 1, w // 16 + 1)).astype(np.float32)
    terrain = cv2.resize(terrain, (w, h), interpolation=cv2.INTER_CUBIC)
    base = np.array([60, 95, 70], np.float32)
    img[horizon:] = base + 12 * terrain[horizon:, :, None]
    return img


def _draw_aircraft(img: np.ndarray, x: float, y: float, size: float, shade: float = 55) -> None:
    """Very simple aircraft silhouette: fuselage + wing, anti-aliased by supersampling."""
    s = 4  # supersampling
    r = int(max(size, 2) * 1.2) + 4
    x0, y0 = int(x) - r, int(y) - r
    patch = np.zeros(((2 * r) * s, (2 * r) * s), np.float32)
    cx, cy = (x - x0) * s, (y - y0) * s
    span = size * s
    cv2.ellipse(patch, (int(cx), int(cy)), (max(1, int(span * 0.18)), max(1, int(span * 0.22))),
                0, 0, 360, 1.0, -1)
    cv2.line(patch, (int(cx - span / 2), int(cy)), (int(cx + span / 2), int(cy)), 1.0,
             max(1, int(span * 0.08)))
    alpha = cv2.resize(patch, (2 * r, 2 * r), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    xs, ys = max(0, x0), max(0, y0)
    xe, ye = min(w, x0 + 2 * r), min(h, y0 + 2 * r)
    if xe <= xs or ye <= ys:
        return
    a = alpha[ys - y0:ye - y0, xs - x0:xe - x0, None]
    img[ys:ye, xs:xe] = img[ys:ye, xs:xe] * (1 - a) + shade * a


def make_synthetic_video(path: str | Path, width: int = 1920, height: int = 1080, fps: float = 30.0,
                         duration_s: float = 30.0, hfov_deg: float = 50.0, seed: int = 7) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    horizon = int(height * 0.72)
    bg = _sky(width, height, horizon, rng)
    f = (width / 2) / math.tan(math.radians(hfov_deg) / 2)

    def px(az: float, el: float) -> tuple[float, float]:
        return width / 2 + f * math.tan(math.radians(az)), height / 2 - f * math.tan(math.radians(el))

    T_contact = duration_s + 2.0
    s0 = 3.0
    collision_xy = px(20.0, 1.0)
    specks = [(width * 0.33, height * 0.25), (width * 0.62, height * 0.40)]

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    n = int(duration_s * fps)
    for i in range(n):
        t = i / fps
        img = bg.copy()
        # 1. collision course: constant bearing, growing
        size = s0 * T_contact / (T_contact - t)
        jx, jy = rng.normal(0, 0.3, 2)
        _draw_aircraft(img, collision_xy[0] + jx, collision_xy[1] + jy, size)
        # 2. crossing traffic: az from -30 to +10 deg over the clip, el +8 deg
        az = -30 + 40 * t / duration_s
        cx, cy = px(az, 8.0)
        _draw_aircraft(img, cx, cy, 4.0)
        # 3. windscreen specks (static, not growing)
        for sx, sy in specks:
            cv2.circle(img, (int(sx), int(sy)), 2, (70, 70, 70), -1)
        # sensor noise
        img += rng.normal(0, 2.0, img.shape).astype(np.float32)
        writer.write(np.clip(img, 0, 255).astype(np.uint8))
    writer.release()

    truth = {
        "video": path.name, "width": width, "height": height, "fps": fps,
        "duration_s": duration_s, "hfov_deg": hfov_deg,
        "targets": [
            {"name": "collision", "azimuth_deg": 20.0, "elevation_deg": 1.0,
             "expected": "WARNING", "contact_time_s": T_contact,
             "pixel": [round(collision_xy[0], 1), round(collision_xy[1], 1)]},
            {"name": "crossing", "azimuth_deg": [-30.0, 10.0], "elevation_deg": 8.0,
             "expected": "no ADVISORY/WARNING"},
            {"name": "windscreen_specks", "pixels": specks, "expected": "suppressed"},
        ],
    }
    path.with_suffix(".json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


# --- synthetic recording session (for the evaluation tool) -----------------------------------------

def make_synthetic_session(parent: str | Path, width: int = 1280, height: int = 720, fps: float = 10.0,
                           duration_s: float = 40.0, seed: int = 11,
                           heading_error_deg: float = 2.0, pitch_error_deg: float = 0.5) -> Path:
    """Write a complete session folder as the recorder would, with known geometry.

    Station at LIPB, camera looking 190 deg true, pitch 3 deg, 50 deg HFOV. Traffic:

    * ``4CA2D6`` ADS-B airliner-sized target approaching from 6 km to ~3 km (drawn)
    * ``DDA5BA`` FLARM glider via OGN, crossing at 3 km, higher (drawn)
    * ``3C6444`` ADS-B jet at 30 km - in the field of view but far too small (not drawn)
    * one non-cooperative paraglider without any transponder (drawn, no traffic report)

    ``session.json`` records the heading/pitch *with an error* of
    ``heading_error_deg``/``pitch_error_deg`` - the evaluation should find it.
    The traffic lines go through the real parsers (SBS, OGN APRS).
    """
    import datetime as dt

    from .config import CameraConfig
    from .geometry import CameraModel
    from .recorder.video import open_writer
    from .sensors.adsb import SbsParser, sbs_report
    from .sensors.ogn import parse_aprs
    from .sensors.traffic import look_angles, offset_position

    rng = np.random.default_rng(seed)
    lat0, lon0, elev = 46.4603, 11.3264, 241.0
    heading, pitch, hfov = 190.0, 3.0, 50.0
    qnh = 1020.0
    t0 = dt.datetime(2026, 10, 20, 8, 0, 0, tzinfo=dt.timezone.utc).timestamp()
    folder = Path(parent) / "20261020-080000Z_SYNTH"
    (folder / "camera").mkdir(parents=True, exist_ok=True)
    (folder / "raw").mkdir(exist_ok=True)

    def at(az: float, dist: float, alt_agl: float) -> tuple[float, float, float]:
        n, e = dist * math.cos(math.radians(az)), dist * math.sin(math.radians(az))
        la, lo = offset_position(lat0, lon0, n, e)
        return la, lo, elev + alt_agl

    def lerp(p, q, x):
        return tuple(a + (b - a) * x for a, b in zip(p, q))

    a0, a1 = at(185, 6000, 550), at(187, 2800, 330)
    g0, g1 = at(172, 3000, 750), at(205, 3100, 760)
    j0, j1 = at(196, 30000, 10500), at(198, 31000, 10500)
    pg = at(200, 1800, 260)

    def pos(name: str, t: float):
        x = t / duration_s
        return {"A": lerp(a0, a1, x), "G": lerp(g0, g1, x), "J": lerp(j0, j1, x),
                "P": (pg[0] + 1e-6 * t, pg[1] + 4e-6 * t, pg[2])}[name]

    span = {"A": 34.0, "G": 15.0, "P": 10.0}
    cam_true = CameraModel(CameraConfig(width=width, height=height, hfov_deg=hfov,
                                        yaw_deg=heading, pitch_deg=pitch))
    horizon_v = cam_true.direction_to_pixel(heading, 0.0)[1]
    bg = _sky(width, height, int(horizon_v), rng)

    # --- video ---
    writer, vpath = open_writer(folder / "camera" / "cam0_000", "mjpg", fps, (width, height))
    stamps = ["segment,frame,t_wall,t_rel"]
    n = int(duration_s * fps)
    for i in range(n):
        t = i / fps
        img = bg.copy()
        for name in ("A", "G", "P"):
            la, lo, al = pos(name, t)
            az, el, rng_m = look_angles(lat0, lon0, elev, la, lo, al)
            uv = cam_true.direction_to_pixel(az, el)
            if uv:
                _draw_aircraft(img, uv[0], uv[1], max(1.5, span[name] / rng_m * cam_true.f))
        img += rng.normal(0, 2.0, img.shape).astype(np.float32)
        writer.write(np.clip(img, 0, 255).astype(np.uint8))
        jitter = rng.normal(0, 0.003)
        stamps.append(f"0,{i},{t0 + t + jitter:.4f},{t + jitter:.4f}")
    writer.release()
    (folder / "camera" / "cam0.timestamps.csv").write_text("\n".join(stamps) + "\n", encoding="utf-8")

    # --- traffic: raw lines -> real parsers -> traffic.jsonl ---
    def baro_ft(alt_msl: float) -> int:
        # inverse of pressure_to_qnh_altitude, rounded to 25 ft like Mode S
        p = qnh * (1 - alt_msl / 44330.8) ** 5.25588
        h = 44330.8 * (1 - (p / 1013.25) ** 0.190263)
        return int(round(h / 0.3048 / 25) * 25)

    def dm(v: float, deg_digits: int) -> tuple[str, int]:
        d = int(v)
        m = round((v - d) * 60, 3)
        thousands = int(round(m * 1000))
        s = f"{d:0{deg_digits}d}{thousands // 1000:02d}.{(thousands % 1000) // 10:02d}"
        return s, thousands % 10

    sbs, aprs, reports = [], [], []
    parser = SbsParser()
    for k in range(int(duration_s * 2) + 4):
        t = -1.0 + k * 0.5
        for icao, name, call in (("4CA2D6", "A", "EIN5AB"), ("3C6444", "J", "DLH9X")):
            la, lo, al = pos(name, t)
            rx = t0 + t + 0.2
            lines = [f"MSG,3,1,1,{icao},1,,,,,,{baro_ft(al)},,,{la:.5f},{lo:.5f},,,0,0,0,0"]
            if k % 4 == 0:
                lines.insert(0, f"MSG,1,1,1,{icao},1,,,,,{call},,,,,,,,,,,")
            for line in lines:
                sbs.append(f"{rx:.3f}\t{line}")
                r = sbs_report(parser, line, rx)
                if r:
                    reports.append(r)
    for k in range(int(duration_s) + 3):
        t = -1.0 + k
        la, lo, al = pos("G", t)
        tt = dt.datetime.fromtimestamp(t0 + round(t), tz=dt.timezone.utc).strftime("%H%M%S")
        la_s, la_d = dm(la, 2)
        lo_s, lo_d = dm(lo, 3)
        line = (f"FLRDDA5BA>OGFLR,qAS,LIPB:/{tt}h{la_s}N/{lo_s}E'110/085/A={int(al / 0.3048):06d}"
                f" !W{la_d}{lo_d}! id06DDA5BA +020fpm +0.1rot 7.2dB 0e")
        rx = t0 + round(t) + 1.5
        aprs.append(f"{rx:.3f}\t{line}")
        r = parse_aprs(line, rx)
        if r:
            reports.append(r)
    reports.sort(key=lambda r: r.rx)
    (folder / "raw" / "adsb_sbs.txt").write_text("\n".join(sbs) + "\n", encoding="utf-8")
    (folder / "raw" / "ogn_aprs.txt").write_text("\n".join(aprs) + "\n", encoding="utf-8")
    (folder / "traffic.jsonl").write_text("".join(r.to_json() + "\n" for r in reports),
                                          encoding="utf-8")

    meta = {
        "format": "ota-session/1", "software": "synthetic", "mode": "ground",
        "started_utc": dt.datetime.fromtimestamp(t0, tz=dt.timezone.utc).isoformat(),
        "station": {"name": "SYNTH", "lat": lat0, "lon": lon0, "elev_m": elev},
        "qnh_hpa": qnh,
        "camera": {"source": "synthetic", "name": "cam0",
                   "actual": {"width": width, "height": height, "format": "synthetic"},
                   "requested": {"fps": fps},
                   "heading_deg": heading + heading_error_deg, "pitch_deg": pitch + pitch_error_deg,
                   "roll_deg": 0.0, "hfov_deg": hfov, "segment_files": {"0": vpath.name},
                   "timestamps": "camera/cam0.timestamps.csv"},
        "truth": {"heading_deg": heading, "pitch_deg": pitch,
                  "targets": {"icao:4CA2D6": "approach, drawn", "flarm:DDA5BA": "glider, drawn",
                              "icao:3C6444": "30 km, not drawn",
                              "non-cooperative": "paraglider at az 200, 1.8 km"}},
    }
    (folder / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return folder


def make_synthetic_flight_session(parent: str | Path, width: int = 1280, height: int = 720,
                                  fps: float = 8.0, duration_s: float = 30.0, crab_deg: float = 3.0,
                                  seed: int = 5, imu: bool = False) -> Path:
    """In-flight session: own aircraft flies north at 50 m/s, camera looks along the nose.

    * own position comes as GPS NMEA ($GPRMC/$GPGGA) through the real parser;
      the GPS track differs from the nose heading by ``crab_deg`` (wind)
    * ``4CA2D6`` (ADS-B) approaches head-on, slightly higher -> drawn
    * from 18 s to 26 s a coordinated right turn (up to 6 deg/s, ~28 deg bank)
    * ``imu=True``: a camera-mounted WitMotion IMU (100 Hz, with gyro bias) is
      simulated and its binary packets go through the real parser into imu.csv
    """
    import datetime as dt

    from .attitude import G, body_angles, rotation
    from .config import CameraConfig
    from .geometry import CameraModel
    from .recorder.video import open_writer
    from .sensors.adsb import SbsParser, sbs_report
    from .sensors.flarm import FlarmNmeaParser
    from .sensors.traffic import enu, offset_position
    from .sensors.witmotion import ImuSample, WitMotionParser, encode_sample

    rng = np.random.default_rng(seed)
    lat0, lon0, alt0 = 46.30, 11.30, 1500.0
    t0 = dt.datetime(2026, 10, 21, 9, 0, 0, tzinfo=dt.timezone.utc).timestamp()
    folder = Path(parent) / ("20261021-090000Z_FLIGHTIMU" if imu else "20261021-090000Z_FLIGHT")
    (folder / "camera").mkdir(parents=True, exist_ok=True)
    (folder / "raw").mkdir(exist_ok=True)
    v_own = 50.0
    w_max = math.radians(6.0)

    # turn-rate profile on a fine grid: ramp 18-20 s, hold to 24 s, ramp down to 26 s
    step = 0.005
    grid = np.arange(-2.0, duration_s + 3.0, step)
    omega = np.interp(grid, [18, 20, 24, 26], [0, w_max, w_max, 0], left=0, right=0)
    nose_rad = np.cumsum(omega) * step
    nose_rad -= np.interp(0.0, grid, nose_rad)
    trk_rad = nose_rad + math.radians(crab_deg)
    north = np.cumsum(v_own * np.cos(trk_rad)) * step
    east = np.cumsum(v_own * np.sin(trk_rad)) * step
    north -= np.interp(0.0, grid, north)
    east -= np.interp(0.0, grid, east)

    def nose(t):
        return math.degrees(float(np.interp(t, grid, nose_rad)))

    def bank(t):
        return math.degrees(math.atan(v_own * float(np.interp(t, grid, omega)) / G))

    def att(t):
        return rotation(nose(t), 0.0, bank(t))

    def own(t):
        la, lo = offset_position(lat0, lon0, float(np.interp(t, grid, north)),
                                 float(np.interp(t, grid, east)))
        return la, lo, alt0

    def target(t):
        la, lo = offset_position(lat0, lon0, 5000 - 45 * t, 150.0)
        return la, lo, alt0 + 80

    hfov = 50.0
    cam = CameraModel(CameraConfig(width=width, height=height, hfov_deg=hfov))
    bg = _sky(width, height, int(cam.direction_to_pixel(0.0, 0.0)[1]), rng)
    writer, vpath = open_writer(folder / "camera" / "cam0_0000", "mjpg", fps, (width, height))
    stamps = ["segment,frame,t_wall,t_rel"]
    for i in range(int(duration_s * fps)):
        t = i / fps
        img = bg.copy()
        d = np.array(enu(*own(t), *target(t)))
        az, el = body_angles(att(t), d)
        uv = cam.direction_to_pixel(az, el)
        if uv:
            _draw_aircraft(img, uv[0], uv[1], max(1.5, 11.0 / float(np.linalg.norm(d)) * cam.f))
        img += rng.normal(0, 2.0, img.shape).astype(np.float32)
        writer.write(np.clip(img, 0, 255).astype(np.uint8))
        stamps.append(f"0,{i},{t0 + t:.4f},{t:.4f}")
    writer.release()
    (folder / "camera" / "cam0.timestamps.csv").write_text("\n".join(stamps) + "\n", encoding="utf-8")

    def nmea(body: str) -> str:
        cs = 0
        for ch in body:
            cs ^= ord(ch)
        return f"${body}*{cs:02X}"

    def dm(v: float, deg_digits: int) -> str:
        d = int(v)
        return f"{d:0{deg_digits}d}{(v - d) * 60:07.4f}"

    reports, raw_gps, raw_sbs = [], [], []
    gps = FlarmNmeaParser()
    sbs = SbsParser()
    for k in range(int(duration_s) + 2):
        t = k - 0.5
        la, lo, al = own(t)
        utc = dt.datetime.fromtimestamp(t0 + t, tz=dt.timezone.utc)
        hms = utc.strftime("%H%M%S") + f".{int(utc.microsecond / 1e4):02d}"
        trk = (nose(t) + crab_deg) % 360
        for body in (f"GPGGA,{hms},{dm(la, 2)},N,{dm(lo, 3)},E,1,9,0.9,{al:.1f},M,47.0,M,,",
                     f"GPRMC,{hms},A,{dm(la, 2)},N,{dm(lo, 3)},E,{v_own / 0.514444:.1f},{trk:.1f},"
                     f"{utc.strftime('%d%m%y')},,,A"):
            line = nmea(body)
            rx = t0 + t + 0.1
            raw_gps.append(f"{rx:.3f}\t{line}")
            reports += gps.feed(line, rx)
    for k in range(int(duration_s * 2) + 2):
        t = -0.5 + k * 0.5
        la, lo, al = target(t)
        line = f"MSG,3,1,1,4CA2D6,1,,,,,,{int(round(al / 0.3048 / 25) * 25)},,,{la:.5f},{lo:.5f},,,0,0,0,0"
        raw_sbs.append(f"{t0 + t:.3f}\t{line}")
        r = sbs_report(sbs, line, t0 + t)
        if r:
            reports.append(r)
    reports.sort(key=lambda r: r.rx)
    (folder / "raw" / "gps_nmea.txt").write_text("\n".join(raw_gps) + "\n", encoding="utf-8")
    (folder / "raw" / "adsb_sbs.txt").write_text("\n".join(raw_sbs) + "\n", encoding="utf-8")
    (folder / "traffic.jsonl").write_text("".join(r.to_json() + "\n" for r in reports), encoding="utf-8")

    if imu:
        parser = WitMotionParser()
        rows = [ImuSample.CSV_HEADER]
        bias = np.array([0.4, -0.3, 0.25])  # deg/s
        chunk, ts_imu = b"", np.arange(-1.0, duration_s + 1.0, 0.01)
        for k, t in enumerate(ts_imu):
            R = att(t)
            dR = (att(t + 0.002) - att(t - 0.002)) / 0.004
            W = R.T @ dR
            w_b = np.degrees([W[2, 1], W[0, 2], W[1, 0]]) + bias + rng.normal(0, 0.05, 3)
            om = float(np.interp(t, grid, omega))
            tr = float(np.interp(t, grid, trk_rad))
            a_n = v_own * om * np.array([math.cos(tr), -math.sin(tr), 0.0])
            f_b = R.T @ (a_n + np.array([0, 0, G])) / G + rng.normal(0, 0.003, 3)
            chunk += encode_sample(ImuSample(t0 + t, *f_b, *w_b, 0.0, 0.0, 0.0))
            if k % 10 == 9:  # USB serial delivers bursts
                for smp in parser.feed(chunk, t0 + t + 0.004):
                    rows.append(smp.to_csv())
                chunk = b""
        (folder / "imu.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    meta = {"format": "ota-session/1", "software": "synthetic", "mode": "aircraft",
            "started_utc": dt.datetime.fromtimestamp(t0, tz=dt.timezone.utc).isoformat(),
            "station": {"name": "FLIGHT", "lat": lat0, "lon": lon0, "elev_m": 241.0},
            "qnh_hpa": 1013.25, "own_icao": "",
            "camera": {"source": "synthetic", "name": "cam0",
                       "actual": {"width": width, "height": height, "format": "synthetic"},
                       "requested": {"fps": fps}, "heading_deg": 0.0, "pitch_deg": 0.0,
                       "roll_deg": 0.0, "hfov_deg": hfov, "segment_files": {"0": vpath.name},
                       "timestamps": "camera/cam0.timestamps.csv"},
            "truth": {"crab_deg": crab_deg, "turn": "18-26 s, up to 6 deg/s, coordinated",
                      "imu": imu}}
    (folder / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return folder
