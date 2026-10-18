"""Evaluate a recorded session against ADS-B / FLARM / OGN positions (``ota evaluate``).

For every video frame and every cooperative aircraft the tool computes where
the aircraft *must* be in the image (station position + camera heading,
pitch and field of view + the aircraft's interpolated position), runs the
normal detection pipeline and checks whether a candidate / confirmed track
is there. The result answers the questions that matter for the project:

* **Detection range** - from how far away is an aircraft detected (single
  frame candidate) and tracked (confirmed track = what the warning logic
  uses)? Probability of detection per range bin.
* **Angular accuracy** - is the clock position right?
* **Camera attitude** - the heading/pitch entered by the user is usually a
  few degrees off. The tool estimates the correction from the matches
  (median offset between expected and detected direction) and applies it.
* **Unmatched tracks** - confirmed tracks without any cooperative aircraft:
  non-cooperative traffic (gliders without FLARM, paragliders, drones), birds
  or false detections. Their crops are saved for review/labelling.
* **Training data** - crops of matched detections are auto-labelled
  ``aircraft`` crops from *our own* camera.

Outputs go to ``<session>/eval/``: ``report.md``, ``summary.json``,
``targets.csv``, ``matches.csv``, ``tracks.csv``, ``crops/`` and optionally
``overlay.mp4``.

Limits: ground sessions (fixed station) are fully supported. For in-flight
sessions the own position comes from the FLARM/GPS ``own`` reports and the
camera azimuth is taken relative to the GPS track (no attitude data yet), so
results are rough.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import CameraConfig, Config, load_config
from .detection.candidates import crop as crop_candidate
from .geometry import CameraModel, wrap180
from .pipeline import CameraPipeline
from .sensors.traffic import TrafficReport, look_angles, pressure_to_qnh_altitude
from .sources import Frame

log = logging.getLogger(__name__)

RANGE_BINS_KM = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50]
MAX_GAP_S = {"adsb": 10.0, "ogn": 20.0, "flarm": 10.0}


# --- loading ----------------------------------------------------------------------------------

@dataclass
class TargetTrack:
    key: str
    callsign: str = ""
    src: set = field(default_factory=set)
    acft_type: str = ""
    t: np.ndarray = None
    lat: np.ndarray = None
    lon: np.ndarray = None
    alt: np.ndarray = None
    gap: np.ndarray = None  # max allowed gap per sample (by source)

    def at(self, t: float) -> tuple[float, float, float] | None:
        """Linear interpolation; up to 2 s extrapolation at the ends."""
        ts = self.t
        if len(ts) == 0 or t < ts[0] - 2.0 or t > ts[-1] + 2.0:
            return None
        if len(ts) == 1:
            return (float(self.lat[0]), float(self.lon[0]), float(self.alt[0])) \
                if abs(t - ts[0]) <= 2.0 else None
        i = int(np.searchsorted(ts, t))
        i = min(max(i, 1), len(ts) - 1)
        t0, t1 = ts[i - 1], ts[i]
        if t1 - t0 > max(self.gap[i - 1], self.gap[i]):
            # inside a long gap: only accept close to one of the samples
            if t - t0 > 2.0 and t1 - t > 2.0:
                return None
        x = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return (float(self.lat[i - 1] + x * (self.lat[i] - self.lat[i - 1])),
                float(self.lon[i - 1] + x * (self.lon[i] - self.lon[i - 1])),
                float(self.alt[i - 1] + x * (self.alt[i] - self.alt[i - 1])))


def load_traffic(path: Path, qnh_hpa: float) -> tuple[dict[str, TargetTrack], list[TrafficReport]]:
    """Group reports by aircraft; convert pressure altitudes to MSL with QNH."""
    by_key: dict[str, list[TrafficReport]] = defaultdict(list)
    own: list[TrafficReport] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = TrafficReport.from_dict(json.loads(line))
            if r.lat is None or r.lon is None:
                continue
            if r.src == "own":
                own.append(r)
            elif r.alt_m is not None:
                by_key[r.key].append(r)
    targets = {}
    for key, reps in by_key.items():
        # prefer GNSS altitudes when the same aircraft also comes via OGN/FLARM
        reps.sort(key=lambda r: r.t)
        rows, last_t = [], None
        for r in reps:
            if last_t is not None and abs(r.t - last_t) < 0.05:
                continue
            alt = r.alt_m if r.alt_ref == "gps" else pressure_to_qnh_altitude(r.alt_m, qnh_hpa)
            rows.append((r.t, r.lat, r.lon, alt, MAX_GAP_S.get(r.src, 10.0)))
            last_t = r.t
        a = np.array(rows, dtype=np.float64)
        tt = TargetTrack(key=key, t=a[:, 0], lat=a[:, 1], lon=a[:, 2], alt=a[:, 3], gap=a[:, 4])
        tt.callsign = next((r.callsign for r in reversed(reps) if r.callsign), "")
        tt.src = {r.src for r in reps}
        tt.acft_type = next((r.acft_type for r in reps if r.acft_type), "")
        targets[key] = tt
    own.sort(key=lambda r: r.t)
    return targets, own


def load_timestamps(path: Path) -> list[tuple[int, int, float]]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((int(r["segment"]), int(r["frame"]), float(r["t_wall"])))
    return rows


# --- per-frame data ---------------------------------------------------------------------------

@dataclass
class Det:
    u: float
    v: float
    size: float
    score: float
    track: int = 0   # confirmed track id, 0 = single-frame candidate only


@dataclass
class FrameData:
    index: int           # global frame number
    seg: int
    seg_frame: int
    t: float             # wall time
    dets: list
    sky: np.ndarray | None  # packed sky mask (1/16 resolution)
    sky_shape: tuple = (0, 0)


def _pack_sky(mask: np.ndarray | None):
    if mask is None:
        return None, (0, 0)
    small = cv2.resize(mask, (max(1, mask.shape[1] // 16), max(1, mask.shape[0] // 16)),
                       interpolation=cv2.INTER_NEAREST) > 0
    return np.packbits(small), small.shape


def _sky_at(fd: FrameData, u: float, v: float, w: int, h: int) -> bool:
    if fd.sky is None:
        return True
    sh, sw = fd.sky_shape
    x, y = int(u / w * sw), int(v / h * sh)
    if not (0 <= x < sw and 0 <= y < sh):
        return False
    idx = y * sw + x
    return bool((fd.sky[idx >> 3] >> (7 - (idx & 7))) & 1)


# --- evaluation -------------------------------------------------------------------------------

class SessionEvaluator:
    def __init__(self, session: str | Path, cfg: Config | None = None, stride: int = 1,
                 gate_deg: float = 1.0, auto_calibrate: bool = True, max_frames: int | None = None,
                 save_crops: bool = True):
        self.dir = Path(session)
        self.meta = json.loads((self.dir / "session.json").read_text(encoding="utf-8"))
        if not self.meta.get("camera"):
            raise ValueError("session has no camera recording")
        self.cfg = cfg or Config()
        self.stride = max(1, stride)
        self.gate_deg = gate_deg
        self.auto_calibrate = auto_calibrate
        self.max_frames = max_frames
        self.save_crops = save_crops
        cam = self.meta["camera"]
        st = self.meta["station"]
        self.station = (st["lat"], st["lon"], st["elev_m"])
        self.qnh = float(self.meta.get("qnh_hpa") or 1013.25)
        self.heading = float(cam["heading_deg"])
        self.pitch = float(cam["pitch_deg"])
        self.roll = float(cam.get("roll_deg") or 0.0)
        self.hfov = float(cam["hfov_deg"])
        self.mode = self.meta.get("mode", "ground")
        self.targets, self.own = load_traffic(self.dir / "traffic.jsonl", self.qnh)
        self.stamps = load_timestamps(self.dir / cam.get("timestamps", "camera/cam0.timestamps.csv"))
        if cam.get("segment_files"):
            self.segment_files = {int(k): v for k, v in cam["segment_files"].items()}
        elif cam.get("segments"):
            self.segment_files = dict(enumerate(cam["segments"]))
        else:
            self.segment_files = dict(enumerate(sorted(
                p.name for p in (self.dir / "camera").glob("cam0_*.*") if p.suffix != ".csv")))
        self.frames: list[FrameData] = []
        self.crops: dict[int, list[tuple[float, np.ndarray]]] = defaultdict(list)
        self.track_points: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        self.width = self.height = 0
        self.offset_az = 0.0
        self.offset_el = 0.0
        self.n_calib = 0

    # -- geometry --

    def camera_model(self, d_az: float = 0.0, d_el: float = 0.0) -> CameraModel:
        """Camera model with a correction added to the entered heading/pitch."""
        return CameraModel(CameraConfig(name="cam0", width=self.width, height=self.height,
                                        hfov_deg=self.hfov, yaw_deg=self.heading + d_az,
                                        pitch_deg=self.pitch + d_el, roll_deg=self.roll))

    def observer(self, t: float) -> tuple[tuple[float, float, float], float] | None:
        """(lat, lon, alt) of the camera and the azimuth reference at time t."""
        if self.mode != "aircraft":
            return self.station, 0.0
        if not self.own:
            return None
        ts = [r.t for r in self.own]
        i = min(max(int(np.searchsorted(ts, t)), 0), len(ts) - 1)
        r = self.own[i]
        if abs(r.t - t) > 5 or r.alt_m is None:
            return None
        return (r.lat, r.lon, r.alt_m), (r.track_deg or 0.0)

    # -- pass 1: run the detector over the video --

    def iter_frames(self):
        """Yield (global index, segment, frame in segment, t_wall, image) in time order.

        A smart-mode session has many short chunk files plus an idle stream that
        overlaps them in time; frames are read from whichever file holds the next
        timestamp. A frame stored twice (idle stream and kept chunk) is used once."""
        rows = sorted(self.stamps, key=lambda r: r[2])
        last_needed: dict[int, int] = {}
        for seg, fr, _ in rows:
            last_needed[seg] = max(fr, last_needed.get(seg, -1))
        caps: dict[int, list] = {}  # seg -> [capture, next frame index]
        prev_t = None
        g = 0
        try:
            for seg, fr, tw in rows:
                if prev_t is not None and abs(tw - prev_t) < 1e-3:
                    continue  # duplicate of the previous frame (idle + chunk)
                if seg not in self.segment_files:
                    continue  # chunk was discarded
                c = caps.get(seg)
                if c is None:
                    path = self.dir / "camera" / self.segment_files[seg]
                    cap = cv2.VideoCapture(str(path))
                    if not cap.isOpened():
                        log.warning("cannot open %s", path)
                        self.segment_files.pop(seg)
                        continue
                    c = caps[seg] = [cap, 0]
                while c[1] < fr:
                    c[0].grab()
                    c[1] += 1
                ok, img = c[0].read()
                c[1] += 1
                if fr >= last_needed[seg]:
                    c[0].release()
                    caps.pop(seg)
                if not ok:
                    continue
                prev_t = tw
                yield g, seg, fr, tw, img
                g += 1
        finally:
            for c in caps.values():
                c[0].release()

    def process_video(self, progress=None) -> None:
        from .tracking import Tracker
        pipe = None
        total = len(self.stamps)
        prev_t = None
        for g, seg, fr, tw, img in self.iter_frames():
            if g % self.stride:
                continue
            if pipe is None:
                self.height, self.width = img.shape[:2]
                cam = CameraConfig(name="cam0", width=self.width, height=self.height,
                                   hfov_deg=self.hfov, yaw_deg=self.heading,
                                   pitch_deg=self.pitch, roll_deg=self.roll)
                pipe = CameraPipeline(self.cfg, cam, self.width, self.height)
                self.t0 = tw
            elif tw - prev_t > 2.0:
                # gap (discarded chunk or idle stream): tracks cannot continue across it
                old = pipe.tracker
                pipe.tracker = Tracker(self.cfg.tracker, "cam0")
                pipe.tracker._ids = old._ids  # keep track ids unique over the session
            prev_t = tw
            res = pipe.process(Frame("cam0", g, tw - self.t0, img))
            dets = [Det(c.x, c.y, c.size, c.score) for c in res.candidates]
            for tr in pipe.tracker.confirmed():
                p = tr.history[-1]
                dets.append(Det(p.x, p.y, p.size, 1.0, track=tr.id))
                self.track_points[tr.id].append((tw, p.x, p.y, p.size))
                if self.save_crops and len(self.track_points[tr.id]) % 5 == 1 \
                        and len(self.crops[tr.id]) < 200:
                    from .detection.candidates import Candidate
                    c = Candidate(p.x, p.y, p.size, p.size, p.size ** 2, p.contrast, "dark")
                    self.crops[tr.id].append((tw, crop_candidate(img, c, 64).copy()))
            sky, shape = _pack_sky(pipe.detector._sky)
            self.frames.append(FrameData(g, seg, fr, tw, dets, sky, shape))
            if progress and g % 50 == 0:
                progress(g, total)
            if self.max_frames and len(self.frames) >= self.max_frames:
                break

    # -- pass 2: expected positions, calibration, matching --

    def _expected(self, model: CameraModel, fd: FrameData):
        """Yield (key, az, el, range_m, u, v) of cooperative targets near the field of view."""
        obs = self.observer(fd.t)
        if obs is None:
            return
        (la0, lo0, al0), az_ref = obs
        for key, tt in self.targets.items():
            p = tt.at(fd.t)
            if p is None:
                continue
            az, el, rng = look_angles(la0, lo0, al0, *p)
            az = (az - az_ref) % 360.0
            uv = model.direction_to_pixel(az, el)
            yield key, az, el, rng, uv

    def calibrate(self, gate_deg: float = 6.0, max_range_m: float = 15000.0) -> None:
        model = self.camera_model()
        d_az, d_el = [], []
        for fd in self.frames:
            exp = [e for e in self._expected(model, fd) if e[4] is not None and e[3] < max_range_m]
            tracks = [d for d in fd.dets if d.track]
            if not exp or not tracks:
                continue
            dirs = [model.pixel_to_direction(d.u, d.v) for d in tracks]
            for key, az, el, rng, uv in exp:
                # skip ambiguous situations: another target close by
                if any(k2 != key and abs(wrap180(az - a2)) < gate_deg and abs(el - e2) < gate_deg
                       for k2, a2, e2, _, _ in exp):
                    continue
                best = None
                for dr in dirs:
                    da, de = wrap180(dr.azimuth_deg - az), dr.elevation_deg - el
                    dist = math.hypot(da, de)
                    if dist <= gate_deg and (best is None or dist < best[0]):
                        best = (dist, da, de)
                if best:
                    d_az.append(best[1])
                    d_el.append(best[2])
        self.n_calib = len(d_az)
        if self.n_calib >= 20:
            # two rounds: coarse median, then median of the pairs close to it
            ma, me = float(np.median(d_az)), float(np.median(d_el))
            close = [(a, e) for a, e in zip(d_az, d_el) if math.hypot(a - ma, e - me) < 1.5]
            if len(close) >= 10:
                ma = float(np.median([a for a, _ in close]))
                me = float(np.median([e for _, e in close]))
            # detections appear at (expected + d) in the entered model, so the camera
            # really looks d further left/down: correct the entered values by -d
            self.offset_az, self.offset_el = -ma, -me

    def match(self) -> dict:
        model = self.camera_model(self.offset_az, self.offset_el)
        w, h = self.width, self.height
        rows = []
        track_votes: dict[int, Counter] = defaultdict(Counter)
        for fd in self.frames:
            dirs = [(d, model.pixel_to_direction(d.u, d.v)) for d in fd.dets]
            for key, az, el, rng, uv in self._expected(model, fd):
                if uv is None or not (0 <= uv[0] < w and 0 <= uv[1] < h):
                    continue
                sky = _sky_at(fd, uv[0], uv[1], w, h)
                ang_size = math.degrees(30.0 / max(rng, 1.0))
                gate = max(self.gate_deg, 0.6 * ang_size)
                best_c = best_t = None
                for d, dr in dirs:
                    dist = math.hypot(wrap180(dr.azimuth_deg - az), dr.elevation_deg - el)
                    if dist > gate:
                        continue
                    if d.track:
                        if best_t is None or dist < best_t[0]:
                            best_t = (dist, d)
                    elif best_c is None or dist < best_c[0]:
                        best_c = (dist, d)
                if best_t:
                    track_votes[best_t[1].track][key] += 1
                rows.append({
                    "frame": fd.index, "t": fd.t, "key": key, "range_m": rng, "az": az, "el": el,
                    "u": uv[0], "v": uv[1], "sky": sky,
                    "cand": best_c is not None or best_t is not None,
                    "track": best_t[1].track if best_t else 0,
                    "err_deg": (best_t or best_c)[0] if (best_t or best_c) else None,
                    "det_u": (best_t or best_c)[1].u if (best_t or best_c) else None,
                    "det_v": (best_t or best_c)[1].v if (best_t or best_c) else None,
                    "size_px": (best_t or best_c)[1].size if (best_t or best_c) else None,
                })
        self.rows = rows
        self.track_target = {tid: votes.most_common(1)[0][0] for tid, votes in track_votes.items()}
        return self.summarise()

    # -- statistics --

    def summarise(self) -> dict:
        rows = self.rows
        per_key: dict[str, list] = defaultdict(list)
        for r in rows:
            per_key[r["key"]].append(r)
        targets = []
        for key, rs in sorted(per_key.items(), key=lambda kv: min(r["range_m"] for r in kv[1])):
            tt = self.targets[key]
            sky = [r for r in rs if r["sky"]]
            cand = [r for r in sky if r["cand"]]
            trk = [r for r in sky if r["track"]]
            first_trk = min(trk, key=lambda r: r["t"]) if trk else None
            errs = [r["err_deg"] for r in trk if r["err_deg"] is not None]
            targets.append({
                "key": key, "callsign": tt.callsign, "sources": sorted(tt.src),
                "type": tt.acft_type,
                "frames_in_view": len(rs), "frames_against_sky": len(sky),
                "range_min_km": round(min(r["range_m"] for r in rs) / 1000, 2),
                "range_max_km": round(max(r["range_m"] for r in rs) / 1000, 2),
                "detected_pct": round(100 * len(cand) / len(sky), 1) if sky else None,
                "tracked_pct": round(100 * len(trk) / len(sky), 1) if sky else None,
                "max_detection_km": round(max(r["range_m"] for r in cand) / 1000, 2) if cand else None,
                "max_track_km": round(max(r["range_m"] for r in trk) / 1000, 2) if trk else None,
                "first_track_km": round(first_trk["range_m"] / 1000, 2) if first_trk else None,
                "median_error_deg": round(float(np.median(errs)), 2) if errs else None,
            })
        bins = []
        for lo, hi in zip(RANGE_BINS_KM[:-1], RANGE_BINS_KM[1:]):
            rs = [r for r in rows if r["sky"] and lo * 1000 <= r["range_m"] < hi * 1000]
            if not rs:
                continue
            bins.append({"range_km": f"{lo}-{hi}", "frames": len(rs),
                         "aircraft": len({r["key"] for r in rs}),
                         "detected_pct": round(100 * sum(r["cand"] for r in rs) / len(rs), 1),
                         "tracked_pct": round(100 * sum(bool(r["track"]) for r in rs) / len(rs), 1)})
        model = self.camera_model(self.offset_az, self.offset_el)
        unmatched = []
        for tid, pts in self.track_points.items():
            if tid in self.track_target or len(pts) < 3:
                continue
            u, v = float(np.median([p[1] for p in pts])), float(np.median([p[2] for p in pts]))
            d = model.pixel_to_direction(u, v)
            unmatched.append({"track": tid, "t_start": round(pts[0][0] - self.t0, 2),
                              "duration_s": round(pts[-1][0] - pts[0][0], 1),
                              "frames": len(pts), "az_deg": round(d.azimuth_deg % 360, 1),
                              "el_deg": round(d.elevation_deg, 1),
                              "size_px": round(float(np.median([p[3] for p in pts])), 1)})
        unmatched.sort(key=lambda x: -x["frames"])
        lat = self._ogn_latency()
        return {
            "session": self.dir.name,
            "frames_processed": len(self.frames), "stride": self.stride,
            "image": [self.width, self.height], "hfov_deg": self.hfov,
            "camera_entered": {"heading_deg": self.heading, "pitch_deg": self.pitch},
            "camera_corrected": {"heading_deg": round(self.heading + self.offset_az, 2),
                                 "pitch_deg": round(self.pitch + self.offset_el, 2)},
            "camera_correction": {"heading_deg": round(self.offset_az, 2),
                                  "pitch_deg": round(self.offset_el, 2),
                                  "pairs": self.n_calib,
                                  "applied": self.auto_calibrate and self.n_calib >= 20},
            "deg_per_px": round(model.deg_per_px, 4),
            "gate_deg": self.gate_deg,
            "aircraft_with_positions": len(self.targets),
            "targets": targets, "range_bins": bins,
            "confirmed_tracks": len(self.track_points),
            "matched_tracks": len(self.track_target),
            "unmatched_tracks": unmatched,
            "ogn_latency_s": lat,
        }

    def _ogn_latency(self) -> float | None:
        path = self.dir / "traffic.jsonl"
        vals = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if '"src":"ogn"' in line:
                    d = json.loads(line)
                    vals.append(d["rx"] - d["t"])
        return round(float(np.median(vals)), 2) if vals else None

    def run(self, progress=None) -> dict:
        self.process_video(progress)
        if not self.frames:
            raise RuntimeError("no frames processed")
        if self.auto_calibrate:
            self.calibrate()
            if self.n_calib < 20:
                self.offset_az = self.offset_el = 0.0
        return self.match()

    # -- outputs --

    def write_outputs(self, summary: dict, out: Path | None = None, overlay: bool = False) -> Path:
        out = out or (self.dir / "eval")
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        with open(out / "matches.csv", "w", newline="", encoding="utf-8") as f:
            cols = ["frame", "t", "key", "range_m", "az", "el", "u", "v", "sky", "cand", "track",
                    "err_deg", "det_u", "det_v", "size_px"]
            wr = csv.DictWriter(f, fieldnames=cols)
            wr.writeheader()
            for r in self.rows:
                wr.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
        with open(out / "targets.csv", "w", newline="", encoding="utf-8") as f:
            if summary["targets"]:
                wr = csv.DictWriter(f, fieldnames=list(summary["targets"][0]))
                wr.writeheader()
                for t in summary["targets"]:
                    wr.writerow({**t, "sources": "+".join(t["sources"])})
        with open(out / "tracks.csv", "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["track", "target", "frames", "t_start", "t_end"])
            for tid, pts in sorted(self.track_points.items()):
                wr.writerow([tid, self.track_target.get(tid, ""), len(pts), round(pts[0][0], 2),
                             round(pts[-1][0], 2)])
        if self.save_crops:
            self._write_crops(out / "crops")
        (out / "report.md").write_text(render_report(summary), encoding="utf-8")
        if overlay:
            self.write_overlay(out / "overlay.mp4")
        return out

    def _write_crops(self, folder: Path) -> None:
        for tid, items in self.crops.items():
            key = self.track_target.get(tid)
            sub = folder / ("aircraft" if key else "review")
            sub.mkdir(parents=True, exist_ok=True)
            tag = key.replace(":", "-") if key else "unmatched"
            for tw, img in items:
                cv2.imwrite(str(sub / f"{self.dir.name}_{tag}_trk{tid}_{tw:.2f}.png"), img)

    def write_overlay(self, path: Path, max_width: int = 1920) -> None:
        """Second pass over the video: expected positions (yellow) and detections."""
        model = self.camera_model(self.offset_az, self.offset_el)
        by_frame = {fd.index: fd for fd in self.frames}
        rows_by_frame: dict[int, list] = defaultdict(list)
        for r in self.rows:
            rows_by_frame[r["frame"]].append(r)
        scale = min(1.0, max_width / self.width)
        size = (int(self.width * scale), int(self.height * scale))
        fps = float((self.meta["camera"].get("requested") or {}).get("fps") or 10) / self.stride
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        for g, seg, fr, tw, img in self.iter_frames():
            fd = by_frame.get(g)
            if fd is None:
                continue
            for d in fd.dets:
                # green: track of a cooperative aircraft, magenta: track without one
                col = ((0, 200, 0) if d.track in self.track_target else (255, 0, 255)) \
                    if d.track else (160, 160, 160)
                r = 14 if d.track else 6
                cv2.rectangle(img, (int(d.u - r), int(d.v - r)), (int(d.u + r), int(d.v + r)), col, 2)
            for r in rows_by_frame.get(fd.index, []):
                tt = self.targets[r["key"]]
                col = (0, 255, 255) if r["track"] else (0, 140, 255)
                rad = max(10, int(self.gate_deg / model.deg_per_px))
                cv2.circle(img, (int(r["u"]), int(r["v"])), rad, col, 2)
                label = f"{tt.callsign or r['key']} {r['range_m'] / 1000:.1f} km"
                cv2.putText(img, label, (int(r["u"]) + rad + 4, int(r["v"])),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
            stamp = time.strftime("%H:%M:%S", time.gmtime(fd.t))
            cv2.putText(img, f"{stamp}Z  +{fd.t - self.t0:.1f}s", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            writer.write(cv2.resize(img, size, interpolation=cv2.INTER_AREA) if scale < 1 else img)
        writer.release()


def render_report(s: dict) -> str:
    c = s["camera_correction"]
    lines = [f"# Evaluation – {s['session']}", "",
             f"* frames processed: {s['frames_processed']} (stride {s['stride']}), "
             f"image {s['image'][0]}×{s['image'][1]}, HFOV {s['hfov_deg']}° "
             f"({s['deg_per_px'] * 3600:.0f} arcsec/px)",
             f"* aircraft with positions: {s['aircraft_with_positions']}, "
             f"confirmed tracks: {s['confirmed_tracks']} (matched {s['matched_tracks']})",
             f"* camera as entered: heading {s['camera_entered']['heading_deg']}°, "
             f"pitch {s['camera_entered']['pitch_deg']}°",
             f"* estimated correction: heading {c['heading_deg']:+.2f}°, pitch {c['pitch_deg']:+.2f}° "
             f"from {c['pairs']} matches ({'applied' if c['applied'] else 'not applied – too few matches'})"
             f" → heading {s['camera_corrected']['heading_deg']}°, pitch {s['camera_corrected']['pitch_deg']}°"]
    if s.get("ogn_latency_s") is not None:
        lines.append(f"* OGN delay (receive − position time): median {s['ogn_latency_s']} s "
                     "(negative or > 10 s: check the computer clock)")
    lines += ["", "## Aircraft", "",
              "| aircraft | callsign | source | type | range in view (km) | against sky | "
              "detected | tracked | max detection (km) | first track (km) | error (°) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    fmt = lambda v, suf="": "–" if v is None else f"{v}{suf}"  # noqa: E731
    for t in s["targets"]:
        lines.append(f"| {t['key']} | {t['callsign']} | {'+'.join(t['sources'])} | {t['type']} | "
                     f"{t['range_min_km']}–{t['range_max_km']} | {t['frames_against_sky']} | "
                     f"{fmt(t['detected_pct'], ' %')} | {fmt(t['tracked_pct'], ' %')} | "
                     f"{fmt(t['max_detection_km'])} | {fmt(t['first_track_km'])} | "
                     f"{fmt(t['median_error_deg'])} |")
    lines += ["", "## Detection probability by range (frames with the aircraft against the sky)", "",
              "| range (km) | frames | aircraft | detected | tracked |", "|---|---|---|---|---|"]
    for b in s["range_bins"]:
        lines.append(f"| {b['range_km']} | {b['frames']} | {b['aircraft']} | {b['detected_pct']} % | "
                     f"{b['tracked_pct']} % |")
    lines += ["", "## Confirmed tracks without a cooperative aircraft", "",
              "Non-cooperative traffic, birds or false detections – see `crops/review/`.", ""]
    if s["unmatched_tracks"]:
        lines += ["| track | start (s) | duration (s) | azimuth (°) | elevation (°) | size (px) |",
                  "|---|---|---|---|---|---|"]
        for u in s["unmatched_tracks"][:50]:
            lines.append(f"| {u['track']} | {u['t_start']} | {u['duration_s']} | {u['az_deg']} | "
                         f"{u['el_deg']} | {u['size_px']} |")
    else:
        lines.append("none")
    lines += ["", "Detected = a candidate (after the classifier) within the gate of "
              f"{s['gate_deg']}° in that frame; tracked = a confirmed track. 'Against sky' excludes "
              "frames where the aircraft is in front of terrain (not searched in phase 1).", ""]
    return "\n".join(lines)


def evaluate_session(session: str | Path, config: str | None = None, **kw) -> tuple[dict, Path]:
    overlay = kw.pop("overlay", False)
    cfg = load_config(config) if config and Path(config).exists() else Config()
    ev = SessionEvaluator(session, cfg, **kw)
    summary = ev.run(progress=lambda g, n: print(f"  {g}/{n} frames", end="\r", flush=True))
    out = ev.write_outputs(summary, overlay=overlay)
    return summary, out
