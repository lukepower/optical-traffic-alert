"""Session recorder: camera video + ADS-B + FLARM/OGN in one self-contained folder.

Made for club members who record material at the airfield (or in flight)
without knowing anything about the pipeline. One session folder contains
everything the evaluation (``ota evaluate``) needs::

    20261020-074512Z_LIPB/
      session.json              settings, station, camera geometry, statistics
      camera/cam0_0000.avi      video (near-lossless MJPEG); 10 s chunks in smart mode,
      camera/cam0_0003.avi      ~1.8 GB segments in 'all' mode
      camera/cam0_idle_0001.avi smart mode: one frame every few seconds
      camera/cam0.timestamps.csv  segment, frame, t_wall (UTC epoch s), t_rel
      traffic.jsonl             one normalised position report per line (all sources)
      raw/adsb_sbs.txt          raw input lines with receive time, for re-processing
      raw/ogn_aprs.txt
      raw/flarm_nmea.txt
      recorder.log

All timestamps are the recording computer's clock (``time.time()``). Frame
times are taken when the frame arrives from the camera driver. Keep the
Windows clock synchronised (Settings > Time > "Sync now"); the evaluation
estimates the clock error from the OGN position timestamps.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .. import __version__
from ..sensors.adsb import SbsParser, sbs_report
from ..sensors.feeds import LineFeed
from ..sensors.flarm import FlarmNmeaParser
from ..sensors.ogn import OGN_SERVER, login_line, parse_aprs
from ..sensors.traffic import TrafficReport, TrafficTable
from .video import CODECS, open_writer

log = logging.getLogger(__name__)

SESSION_FORMAT = "ota-session/1"


@dataclass
class SessionSettings:
    """Everything the user can set in the recorder window (persisted as JSON)."""

    operator: str = ""
    station_name: str = "LIPB"
    lat: float = 46.4603            # camera position, WGS84
    lon: float = 11.3264
    elev_m: float = 241.0           # camera height above mean sea level (m)
    mode: str = "ground"            # ground | aircraft
    # camera
    camera_enabled: bool = True
    camera_source: str = "usb:0"    # usb:<index> or file:<path> (tests, re-recording)
    width: int = 3840
    height: int = 2160
    fps: float = 15.0
    pixel_format: str = "YUY2"
    codec: str = "mjpg"
    exposure: float | None = None
    gain: float | None = None
    cam_heading_deg: float = 190.0  # true direction the camera looks at (ground) / yaw vs. nose (aircraft)
    cam_pitch_deg: float = 3.0
    cam_roll_deg: float = 0.0
    hfov_deg: float = 50.0
    # traffic sources
    adsb_enabled: bool = True
    adsb_address: str = "127.0.0.1:30003"
    dump1090_path: str = ""         # optional: start dump1090.exe automatically
    dump1090_args: str = "--net --quiet"
    ogn_online: bool = True
    ogn_radius_km: float = 50.0
    ogn_local: bool = False
    ogn_local_address: str = "192.168.1.50:50001"
    flarm_enabled: bool = False
    flarm_address: str = "COM4@19200"
    # misc
    qnh_hpa: float = 1013.25
    notes: str = ""
    output_dir: str = str(Path.home() / "OTA-Sessions")
    # smart recording (see smart.py): full rate only around traffic / motion
    record_mode: str = "smart"      # smart | all
    motion_trigger: bool = True
    trigger_radius_km: float = 15.0
    chunk_s: float = 10.0
    pre_s: float = 20.0
    post_s: float = 20.0
    idle_interval_s: float = 3.0
    segment_mb: int = 1800
    min_free_gb: float = 3.0
    language: str = "de"

    @classmethod
    def load(cls, path: str | Path) -> "SessionSettings":
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def open_capture(source: str, s: SessionSettings) -> cv2.VideoCapture:
    kind, _, arg = source.partition(":")
    if kind == "file":
        cap = cv2.VideoCapture(arg)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video file {arg}")
        return cap
    from ..sources import open_usb_camera
    return open_usb_camera(int(arg or 0), s.width, s.height, s.fps, s.pixel_format,
                           s.exposure, s.gain)


# --- camera ---------------------------------------------------------------------------------------

@dataclass
class CameraStatus:
    open: bool = False
    width: int = 0
    height: int = 0
    format: str = ""
    frames: int = 0
    written: int = 0           # frames written to disk (kept or still pending)
    dropped: int = 0
    fps: float = 0.0
    bytes: int = 0             # size of the kept video files
    kept_s: float = 0.0        # smart mode: seconds of full-rate video kept ...
    discarded_s: float = 0.0   # ... and deleted
    idle_frames: int = 0       # smart mode: frames in the idle stream
    sharpness: float = 0.0     # variance of the Laplacian in the image centre (focus aid)
    brightness: float = 0.0    # mean grey value 0..255
    clipped_pct: float = 0.0   # share of saturated pixels (exposure aid)
    error: str = ""


class _Stream:
    """One open video file plus its timestamp rows."""

    def __init__(self, sid: int, path: Path, writer, start: float):
        self.sid, self.path, self.writer = sid, path, writer
        self.start = self.end = start
        self.rows: list[str] = []
        self.frames = 0

    def write(self, img: np.ndarray, t_wall: float, t_rel: float) -> str:
        self.writer.write(img)
        row = f"{self.sid},{self.frames},{t_wall:.4f},{t_rel:.4f}\n"
        self.frames += 1
        self.end = t_wall
        return row

    def size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            self.writer = None


class CameraRecorder:
    """Capture thread -> queue -> writer thread. With ``out_dir=None`` it only previews.

    Capture and encoding run in separate threads so a slow disk or encoder never
    stalls the camera; if the queue is full the frame is dropped and counted.

    ``record_mode='all'`` keeps everything (files split at ``segment_mb``).
    ``record_mode='smart'`` writes 10 s chunks and keeps only those near a
    trigger, plus a low-rate idle stream - see ``smart.py``."""

    def __init__(self, s: SessionSettings, out_dir: Path | None, name: str = "cam0",
                 triggers=None):
        from .smart import TriggerLog
        self.s = s
        self.out_dir = out_dir
        self.name = name
        self.smart = out_dir is not None and s.record_mode == "smart"
        self.triggers = triggers or TriggerLog()
        self.motion = None
        self.status = CameraStatus()
        self.preview: np.ndarray | None = None   # small BGR image for the GUI
        self._stop = threading.Event()
        self._q: queue.Queue = queue.Queue(maxsize=max(4, int(s.fps * 2)))
        self._threads: list[threading.Thread] = []
        self.cap = None
        self.segment_files: dict[int, str] = {}  # segment id -> file name (kept files only)

    @property
    def segments(self) -> list[str]:
        return [self.segment_files[k] for k in sorted(self.segment_files)]

    def start(self) -> "CameraRecorder":
        from ..sources import fourcc_str
        self.cap = open_capture(self.s.camera_source, self.s)
        ok, img = self.cap.read()
        if not ok or img is None:
            self.cap.release()
            raise RuntimeError(f"camera {self.s.camera_source} delivers no image")
        st = self.status
        st.open, st.height, st.width = True, img.shape[0], img.shape[1]
        st.format = fourcc_str(self.cap.get(cv2.CAP_PROP_FOURCC)) or "?"
        if self.smart and self.s.motion_trigger:
            from .smart import MotionTrigger
            self.motion = MotionTrigger(self.triggers, st.width, st.height).start()
        self._threads = [threading.Thread(target=self._capture, name="cam-capture", daemon=True)]
        if self.out_dir is not None:
            self._threads.append(threading.Thread(target=self._write, name="cam-writer",
                                                  daemon=True))
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=30)
        if self.motion:
            self.motion.stop()
        if self.cap is not None:
            self.cap.release()
        self.status.open = False

    def _capture(self) -> None:
        st = self.status
        wall0, perf0 = time.time(), time.perf_counter()
        last_preview = 0.0
        n_rate, t_rate = 0, time.perf_counter()
        is_file = self.s.camera_source.startswith("file:")
        while not self._stop.is_set():
            if is_file:  # play files at the configured frame rate, like a camera
                time.sleep(max(0.0, perf0 + st.frames / self.s.fps - time.perf_counter()))
            ok, img = self.cap.read()
            now = time.perf_counter()
            if not ok or img is None:
                if is_file:
                    break
                st.error = "camera read failed"
                time.sleep(0.05)
                continue
            t_rel = now - perf0
            st.frames += 1
            n_rate += 1
            if now - t_rate >= 1.0:
                st.fps, n_rate, t_rate = n_rate / (now - t_rate), 0, now
            if self.motion:
                self.motion.offer(wall0 + t_rel, img)
            if self.out_dir is not None:
                try:
                    self._q.put_nowait((st.frames - 1, wall0 + t_rel, t_rel, img))
                except queue.Full:
                    st.dropped += 1
            if now - last_preview > 0.3:
                last_preview = now
                self._update_preview(img)
        self._q.put(None)

    def _update_preview(self, img: np.ndarray) -> None:
        h, w = img.shape[:2]
        scale = 640.0 / w
        self.preview = cv2.resize(img, (640, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        c = gray[h // 2 - min(h, 512) // 2: h // 2 + min(h, 512) // 2,
                 w // 2 - min(w, 512) // 2: w // 2 + min(w, 512) // 2]
        st = self.status
        st.sharpness = float(cv2.Laplacian(c, cv2.CV_32F).var())
        small = gray[::4, ::4]
        st.brightness = float(small.mean())
        st.clipped_pct = float((small >= 250).mean() * 100)

    # -- writer --

    def _write(self) -> None:
        s, st = self.s, self.status
        cam_dir = self.out_dir / "camera"
        cam_dir.mkdir(parents=True, exist_ok=True)
        stamps = open(cam_dir / f"{self.name}.timestamps.csv", "w", encoding="utf-8")
        stamps.write("segment,frame,t_wall,t_rel\n")
        limit = s.segment_mb * 1024 * 1024
        next_id = 0
        kept_bytes = 0
        chunk: _Stream | None = None
        idle: _Stream | None = None
        pending: list[_Stream] = []
        last_idle = -math.inf
        first_t: float | None = None

        def open_stream(prefix: str, t: float, img: np.ndarray, fps: float) -> _Stream:
            nonlocal next_id
            sid = next_id
            next_id += 1
            w, p = open_writer(cam_dir / f"{self.name}_{prefix}{sid:04d}", s.codec, fps,
                               (img.shape[1], img.shape[0]))
            return _Stream(sid, p, w, t)

        def keep(stream: _Stream) -> None:
            nonlocal kept_bytes
            stream.close()
            self.segment_files[stream.sid] = stream.path.name
            kept_bytes += stream.size()
            if stream.rows:
                stamps.writelines(stream.rows)
                stream.rows = []
                stamps.flush()

        def decide(now: float, final: bool = False) -> None:
            while pending and (final or pending[0].end + s.pre_s <= now):
                c = pending.pop(0)
                c.close()
                dur = c.end - c.start + 1.0 / max(s.fps, 1e-3)
                if self.triggers.any_between(c.start - s.post_s, c.end + s.pre_s):
                    keep(c)
                    st.kept_s += dur
                else:
                    try:
                        c.path.unlink()
                    except OSError:  # pragma: no cover
                        log.warning("cannot delete %s", c.path)
                    st.discarded_s += dur

        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                _, t_wall, t_rel, img = item
                if first_t is None:
                    first_t = t_wall
                # 1. idle stream (smart mode): one frame every idle_interval_s, always kept
                if self.smart and t_wall - last_idle >= s.idle_interval_s:
                    if idle is None or (st.idle_frames % 50 == 0 and idle.size() >= limit):
                        if idle is not None:
                            keep(idle)
                        idle = open_stream("idle_", t_wall, img, max(1.0, 1.0 / s.idle_interval_s))
                        self.segment_files[idle.sid] = idle.path.name
                    stamps.write(idle.write(img, t_wall, t_rel))
                    st.idle_frames += 1
                    last_idle = t_wall
                # 2. main stream
                roll = chunk is None
                if chunk is not None and self.smart and t_wall - chunk.start >= s.chunk_s:
                    roll = True
                if chunk is not None and chunk.frames % 30 == 29 and chunk.size() >= limit:
                    roll = True
                if roll:
                    if chunk is not None:
                        chunk.close()
                        if self.smart:
                            pending.append(chunk)
                        else:
                            keep(chunk)
                    chunk = open_stream("", t_wall, img, s.fps)
                    if not self.smart:
                        self.segment_files[chunk.sid] = chunk.path.name
                row = chunk.write(img, t_wall, t_rel)
                if self.smart:
                    chunk.rows.append(row)
                    decide(t_wall)
                else:
                    stamps.write(row)
                st.written += 1
                if st.written % 30 == 0:
                    stamps.flush()
                    st.bytes = kept_bytes + (0 if self.smart else chunk.size())
        except Exception as e:  # disk full, codec error
            st.error = str(e)
            log.exception("camera writer failed")
            self._stop.set()
        finally:
            if chunk is not None:
                if self.smart:
                    chunk.close()
                    pending.append(chunk)
                else:
                    keep(chunk)
            decide(math.inf, final=True)
            if idle is not None:
                keep(idle)
            if not self.smart and first_t is not None and chunk is not None:
                st.kept_s = chunk.end - first_t
            st.bytes = sum((cam_dir / f).stat().st_size for f in self.segment_files.values()
                           if (cam_dir / f).exists())
            stamps.close()


# --- traffic --------------------------------------------------------------------------------------

class TrafficLogger:
    """Writes normalised reports to traffic.jsonl and raw lines to raw/<name>.txt."""

    def __init__(self, out_dir: Path | None):
        self.table = TrafficTable()
        self._lock = threading.Lock()
        self._jsonl = None
        self._raw: dict[str, object] = {}
        self.out_dir = out_dir
        self.keys: set[str] = set()
        if out_dir is not None:
            (out_dir / "raw").mkdir(parents=True, exist_ok=True)
            self._jsonl = open(out_dir / "traffic.jsonl", "a", encoding="utf-8", buffering=1)

    def raw(self, name: str, line: str, rx: float) -> None:
        if self.out_dir is None:
            return
        with self._lock:
            f = self._raw.get(name)
            if f is None:
                f = self._raw[name] = open(self.out_dir / "raw" / f"{name}.txt", "a",
                                           encoding="utf-8", buffering=1)
            f.write(f"{rx:.3f}\t{line}\n")

    def report(self, r: TrafficReport | None) -> None:
        if r is None or r.lat is None:
            return
        self.table.add(r)
        with self._lock:
            if r.src != "own":
                self.keys.add(r.key)
            if self._jsonl:
                self._jsonl.write(r.to_json() + "\n")

    def close(self) -> None:
        with self._lock:
            for f in [self._jsonl, *self._raw.values()]:
                if f:
                    f.close()
            self._jsonl, self._raw = None, {}


def make_feeds(s: SessionSettings, logger: TrafficLogger) -> list[LineFeed]:
    feeds: list[LineFeed] = []
    if s.adsb_enabled and s.adsb_address:
        sbs = SbsParser()

        def on_sbs(line: str, rx: float) -> None:
            logger.raw("adsb_sbs", line, rx)
            logger.report(sbs_report(sbs, line, rx))
        feeds.append(LineFeed("ADS-B", s.adsb_address, on_sbs))

    def on_aprs_factory(name: str):
        def on_aprs(line: str, rx: float) -> None:
            if line.startswith("#"):
                return  # server banners / keepalives
            logger.raw(name, line, rx)
            logger.report(parse_aprs(line, rx))
        return on_aprs

    if s.ogn_online:
        feeds.append(LineFeed("OGN online", OGN_SERVER, on_aprs_factory("ogn_aprs"),
                              login=login_line(s.lat, s.lon, s.ogn_radius_km),
                              keepalive="#keepalive", keepalive_s=240))
    if s.ogn_local and s.ogn_local_address:
        feeds.append(LineFeed("OGN receiver", s.ogn_local_address,
                              on_aprs_factory("ogn_local_aprs")))
    if s.flarm_enabled and s.flarm_address:
        nmea = FlarmNmeaParser()

        def on_nmea(line: str, rx: float) -> None:
            logger.raw("flarm_nmea", line, rx)
            for r in nmea.feed(line, rx):
                logger.report(r)
        feeds.append(LineFeed("FLARM", s.flarm_address, on_nmea))
    return feeds


# --- session --------------------------------------------------------------------------------------

def session_name(s: SessionSettings, t: float | None = None) -> str:
    stamp = dt.datetime.fromtimestamp(t or time.time(), tz=dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    station = "".join(ch for ch in s.station_name if ch.isalnum() or ch in "-_") or "station"
    return f"{stamp}_{station}"


class SessionRecorder:
    def __init__(self, s: SessionSettings):
        self.s = s
        self.dir: Path | None = None
        self.camera: CameraRecorder | None = None
        self.feeds: list[LineFeed] = []
        self.logger: TrafficLogger | None = None
        self.t_start = 0.0
        self.running = False
        self._dump1090: subprocess.Popen | None = None
        self._log_handler: logging.Handler | None = None
        self.error = ""
        from .smart import TriggerLog
        self.triggers = TriggerLog()
        self.traffic_hits: list[str] = []
        self._trig_stop = threading.Event()
        self._trig_thread: threading.Thread | None = None

    # -- lifecycle --

    def start(self) -> Path:
        s = self.s
        if s.camera_enabled and s.codec not in CODECS:
            raise ValueError(f"unknown codec {s.codec}")
        out = Path(os.path.expanduser(s.output_dir))
        out.mkdir(parents=True, exist_ok=True)
        self.t_start = time.time()
        self.dir = out / session_name(s, self.t_start)
        self.dir.mkdir(parents=True)
        self._log_handler = logging.FileHandler(self.dir / "recorder.log", encoding="utf-8")
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        ota_log = logging.getLogger("ota")
        if ota_log.getEffectiveLevel() > logging.INFO:
            ota_log.setLevel(logging.INFO)
        ota_log.addHandler(self._log_handler)
        log.info("session %s started (ota %s)", self.dir.name, __version__)
        self._write_meta()
        try:
            if s.camera_enabled:
                self.camera = CameraRecorder(s, self.dir, triggers=self.triggers).start()
                log.info("camera %s: %dx%d %s", s.camera_source, self.camera.status.width,
                         self.camera.status.height, self.camera.status.format)
            self._start_dump1090()
            self.logger = TrafficLogger(self.dir)
            self.feeds = [f.start() for f in make_feeds(s, self.logger)]
            if self.camera and self.camera.smart:
                self._trig_thread = threading.Thread(target=self._traffic_trigger_loop,
                                                     name="traffic-trigger", daemon=True)
                self._trig_thread.start()
        except Exception:
            self.stop()
            raise
        self.running = True
        self._write_meta()
        return self.dir

    def stop(self) -> Path | None:
        self._trig_stop.set()
        if self._trig_thread:
            self._trig_thread.join(timeout=3)
        for f in self.feeds:
            f.stop()
        if self.camera:
            self.camera.stop()
        if self.logger:
            self.logger.close()
        if self._dump1090 and self._dump1090.poll() is None:
            self._dump1090.terminate()
            try:
                self._dump1090.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self._dump1090.kill()
        self.running = False
        if self.dir:
            self._write_meta(ended=time.time())
            log.info("session %s stopped", self.dir.name)
        if self._log_handler:
            logging.getLogger("ota").removeHandler(self._log_handler)
            self._log_handler.close()
            self._log_handler = None
        return self.dir

    def _traffic_trigger_loop(self) -> None:
        """Smart mode: mark 'traffic' whenever a cooperative aircraft is near the field of view."""
        from .smart import traffic_in_view
        s = self.s
        st = self.camera.status
        aspect = st.width / st.height if st.height else 16 / 9
        while not self._trig_stop.wait(1.0):
            if s.mode == "aircraft":
                own = self.logger.table.ownship()
                if own is None or own.lat is None or own.track_deg is None:
                    observer, heading = None, 0.0
                else:
                    observer = (own.lat, own.lon, own.alt_m if own.alt_m is not None else s.elev_m)
                    heading = own.track_deg + s.cam_heading_deg
            else:
                observer, heading = (s.lat, s.lon, s.elev_m), s.cam_heading_deg
            hits = traffic_in_view(self.logger.table, observer, heading, s.cam_pitch_deg,
                                   s.hfov_deg, aspect, s.trigger_radius_km)
            self.traffic_hits = hits
            if hits:
                self.triggers.add(time.time(), "traffic")

    def _start_dump1090(self) -> None:
        path = self.s.dump1090_path.strip()
        if not (self.s.adsb_enabled and path):
            return
        exe = Path(path)
        if not exe.exists():
            raise FileNotFoundError(f"dump1090 not found: {exe}")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._dump1090 = subprocess.Popen([str(exe), *self.s.dump1090_args.split()], cwd=exe.parent,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=flags)
        log.info("started %s", exe)

    # -- metadata / status --

    def _write_meta(self, ended: float | None = None) -> None:
        s = self.s
        cam = self.camera.status if self.camera else None
        iso = lambda t: dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat(timespec="seconds")  # noqa: E731
        meta = {
            "format": SESSION_FORMAT,
            "software": f"optical-traffic-alert {__version__}",
            "platform": platform.platform(),
            "started_utc": iso(self.t_start),
            "ended_utc": iso(ended) if ended else None,
            "operator": s.operator,
            "notes": s.notes,
            "mode": s.mode,
            "station": {"name": s.station_name, "lat": s.lat, "lon": s.lon, "elev_m": s.elev_m},
            "qnh_hpa": s.qnh_hpa,
            "camera": None if not s.camera_enabled else {
                "source": s.camera_source, "name": "cam0",
                "requested": {"width": s.width, "height": s.height, "fps": s.fps,
                              "pixel_format": s.pixel_format, "codec": s.codec,
                              "exposure": s.exposure, "gain": s.gain},
                "actual": None if cam is None else {"width": cam.width, "height": cam.height,
                                                    "format": cam.format},
                "heading_deg": s.cam_heading_deg, "pitch_deg": s.cam_pitch_deg,
                "roll_deg": s.cam_roll_deg, "hfov_deg": s.hfov_deg,
                "segment_files": {str(k): v for k, v in sorted(self.camera.segment_files.items())}
                if self.camera else {},
                "timestamps": "camera/cam0.timestamps.csv",
            },
            "recording": {"mode": s.record_mode, "motion_trigger": s.motion_trigger,
                          "trigger_radius_km": s.trigger_radius_km, "chunk_s": s.chunk_s,
                          "pre_s": s.pre_s, "post_s": s.post_s,
                          "idle_interval_s": s.idle_interval_s},
            "traffic_sources": {
                "adsb": s.adsb_address if s.adsb_enabled else None,
                "ogn_online": f"{OGN_SERVER} r={s.ogn_radius_km:.0f}km" if s.ogn_online else None,
                "ogn_local": s.ogn_local_address if s.ogn_local else None,
                "flarm": s.flarm_address if s.flarm_enabled else None,
            },
            "stats": self.stats(),
        }
        (self.dir / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def stats(self) -> dict:
        d: dict = {"duration_s": round(time.time() - self.t_start, 1) if self.t_start else 0}
        if self.camera:
            c = self.camera.status
            d["camera"] = {"frames": c.frames, "written": c.written, "dropped": c.dropped,
                           "bytes": c.bytes, "files": len(self.camera.segment_files),
                           "kept_s": round(c.kept_s, 1), "discarded_s": round(c.discarded_s, 1),
                           "idle_frames": c.idle_frames}
            d["triggers"] = dict(self.triggers.counts)
        if self.logger:
            d["traffic"] = {"reports": dict(self.logger.table.counts),
                            "aircraft": len(self.logger.keys)}
        return d

    def disk_free_gb(self) -> float:
        try:
            return shutil.disk_usage(self.dir or Path(self.s.output_dir).expanduser()).free / 1e9
        except OSError:
            return float("nan")

    def check(self) -> None:
        """Call periodically (GUI timer): stops the camera when the disk runs full."""
        if self.camera and self.camera.status.open and self.disk_free_gb() < self.s.min_free_gb:
            self.error = "disk almost full - camera recording stopped"
            log.warning(self.error)
            self.camera.stop()
