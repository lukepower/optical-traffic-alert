"""Command line interface.

    ota synth  out/test.mp4                 # create a synthetic test video
    ota run    --video out/test.mp4 --show  # run the pipeline on a video
    ota run    --config config/default.yaml --aircraft v35a   # live cameras
    ota record --camera usb:0 --out data/raw/flight1.avi      # record raw footage
    ota session --heading 190 --hfov 50                       # camera + ADS-B + FLARM/OGN session
    ota evaluate <session folder>          # compare detections with ADS-B/FLARM positions
    ota ingest E:\\ --s3 ota1:...          # import + evaluate incoming sessions
    ota review                             # sort unclear crops by key press
    ota dataset --out data/crops_own       # labelled crops -> train/val
    ota probe  --camera usb:0               # what does the camera really deliver?
    ota bench                               # how fast is this machine?
    ota cameras                             # list USB cameras that OpenCV can open
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__


def _cmd_synth(args) -> int:
    from .synth import make_synthetic_video
    truth = make_synthetic_video(args.out, width=args.width, height=args.height,
                                 fps=args.fps, duration_s=args.duration)
    print(f"Wrote {args.out} ({truth['width']}x{truth['height']}, {truth['duration_s']}s)")
    print(f"Ground truth: {Path(args.out).with_suffix('.json')}")
    return 0


def _cmd_run(args) -> int:
    from .config import CameraConfig, load_config
    from .pipeline import Runner
    from .tracking import ThreatLevel

    overrides: dict = {"output": {}}
    if args.show:
        overrides["output"]["show"] = True
    if args.record:
        overrides["output"]["record_path"] = args.record
    if args.audio:
        overrides["audio"] = {"backend": args.audio}
    if args.language:
        overrides.setdefault("audio", {})["language"] = args.language
    if args.stride:
        overrides["detector"] = {"frame_stride": args.stride}
    cfg = load_config(args.config, args.aircraft, overrides)

    if args.video:
        cam = cfg.cameras[0] if cfg.cameras else CameraConfig()
        cam.source = f"file:{args.video}"
        if args.hfov:
            cam.hfov_deg = args.hfov
        cfg.cameras = [cam]

    runner = Runner(cfg, realtime=args.realtime)
    t0 = time.perf_counter()
    results = runner.run(max_frames=args.max_frames)
    wall = time.perf_counter() - t0

    if not results:
        print("No frames processed.")
        return 1
    worst = max((a.level for r in results for a in r.assessments), default=ThreatLevel.NONE)
    avg_ms = sum(r.proc_ms for r in results) / len(results)
    print(f"\nFrames: {len(results)}  avg processing: {avg_ms:.1f} ms/frame "
          f"({1000 / avg_ms:.1f} fps possible)  wall: {wall:.1f}s")
    print(f"Highest alert level: {worst.name}")
    print(f"Callouts: {len(runner.announcer.spoken)}")
    for text in runner.announcer.spoken:
        print(f"  - {text}")
    if cfg.output.event_log:
        print(f"Event log: {cfg.output.event_log}")
    time.sleep(0.2)  # let the audio thread finish the last phrase
    return 0


from .recorder.video import CODECS, open_writer as _open_writer  # noqa: E402


def _cmd_record(args) -> int:
    from .config import CameraConfig
    from .sources import UsbCameraSource

    index = int(args.camera.split(":")[-1])
    cam = CameraConfig(name="rec", source=args.camera, width=args.width, height=args.height,
                       fps=args.fps, pixel_format=args.format, exposure=args.exposure,
                       gain=args.gain)
    # record every frame the camera delivers (no dropping)
    src = UsbCameraSource(cam, index, latest_only=False)
    w, h = src.size
    writer, path = _open_writer(args.out, args.codec, args.fps, (w, h))
    stamps = open(path.with_suffix(".timestamps.csv"), "w", encoding="utf-8")
    stamps.write("frame,t_s\n")
    print(f"Recording {w}x{h} {src.format} from {args.camera} to {path} ({args.codec}) "
          f"- Ctrl+C to stop")
    n = 0
    t_last = 0.0
    try:
        for fr in src.frames():
            writer.write(fr.image)
            stamps.write(f"{fr.index},{fr.t:.4f}\n")
            n += 1
            t_last = fr.t
            if n % 100 == 0:
                print(f"  {n} frames, {n / max(fr.t, 1e-6):.1f} fps", flush=True)
            if args.seconds and fr.t >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        writer.release()
        stamps.close()
        src.close()
    fps = n / t_last if t_last > 0 else 0
    print(f"{n} frames in {t_last:.1f}s = {fps:.1f} fps (file is tagged {args.fps} fps; "
          f"real timing in {path.with_suffix('.timestamps.csv').name})")
    return 0


def _cmd_session(args) -> int:
    """Record camera + traffic into one session folder (headless version of ota-recorder)."""
    from .recorder import SessionRecorder, SessionSettings

    st = SessionSettings.load(args.settings) if args.settings else SessionSettings()
    for name, attr in (("out", "output_dir"), ("camera", "camera_source"), ("station", "station_name"),
                       ("lat", "lat"), ("lon", "lon"), ("elev", "elev_m"), ("heading", "cam_heading_deg"),
                       ("pitch", "cam_pitch_deg"), ("hfov", "hfov_deg"), ("width", "width"),
                       ("height", "height"), ("fps", "fps"), ("format", "pixel_format"),
                       ("qnh", "qnh_hpa"), ("notes", "notes"), ("operator", "operator"),
                       ("ogn_radius", "ogn_radius_km")):
        v = getattr(args, name)
        if v is not None:
            setattr(st, attr, v)
    if args.no_camera:
        st.camera_enabled = False
    if args.adsb is not None:
        st.adsb_enabled, st.adsb_address = bool(args.adsb), args.adsb
    if args.ogn_online is not None:
        st.ogn_online = args.ogn_online
    if args.ogn_local:
        st.ogn_local, st.ogn_local_address = True, args.ogn_local
    if args.flarm:
        st.flarm_enabled, st.flarm_address = True, args.flarm
    if args.gps:
        st.gps_enabled, st.gps_address = True, args.gps
    if args.mode:
        st.mode = args.mode
    if args.imu:
        st.imu_enabled, st.imu_address = True, args.imu
    if args.own_icao:
        st.own_icao = args.own_icao
    if args.record_mode:
        st.record_mode = args.record_mode

    rec = SessionRecorder(st)
    folder = rec.start()
    print(f"Recording session {folder} - Ctrl+C to stop")
    t0 = time.time()
    try:
        while True:
            time.sleep(1)
            rec.check()
            el = time.time() - t0
            if int(el) % 5 == 0:
                parts = [f"{el:5.0f}s"]
                if rec.camera:
                    c = rec.camera.status
                    parts.append(f"cam {c.written} frames {c.fps:.1f} fps, {c.dropped} dropped, "
                                 f"{c.bytes / 1e9:.2f} GB")
                for f in rec.feeds:
                    parts.append(f"{f.name}: {'ok' if f.connected else 'offline'} {f.lines} lines")
                parts.append(f"{len(rec.logger.keys)} aircraft")
                print(" | ".join(parts), flush=True)
            if args.seconds and el >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    rec.stop()
    print(f"Done: {folder}")
    print(json.dumps(rec.stats(), indent=2))
    return 0


def _cmd_evaluate(args) -> int:
    from .evaluate import evaluate_session
    summary, out = evaluate_session(args.session, config=args.config, stride=args.stride,
                                    gate_deg=args.gate, auto_calibrate=not args.no_calibrate,
                                    max_frames=args.max_frames, overlay=args.overlay)
    print()
    print((out / "report.md").read_text(encoding="utf-8"))
    print(f"Results in {out}")
    return 0


def _cmd_synth_session(args) -> int:
    from .synth import make_synthetic_session
    folder = make_synthetic_session(args.out)
    print(f"Wrote synthetic session {folder}")
    print(f"Try: ota evaluate {folder} --overlay")
    return 0


def _cmd_upload_code(args) -> int:
    from .recorder.upload import UploadConfig
    cfg = UploadConfig(endpoint=args.endpoint, bucket=args.bucket, key_id=args.key_id,
                       secret=args.secret, region=args.region, prefix=args.prefix)
    if args.check:
        cfg.client().put_object(Bucket=cfg.bucket, Key=f"{cfg.prefix}/_check.txt".lstrip("/"),
                                Body=b"ota upload check\n")
        print("Test upload OK.")
    print(cfg.to_code())
    return 0


def _cmd_upload(args) -> int:
    import os
    from .recorder.upload import SessionUploader, UploadConfig
    code = args.code or os.environ.get("OTA_UPLOAD_CODE", "")
    cfg = UploadConfig.from_code(code)
    for folder in args.sessions:
        up = SessionUploader(folder, cfg, on_progress=lambda p: print(
            f"  {p.done_bytes / 1e9:6.2f}/{p.total_bytes / 1e9:.2f} GB  {p.rate_bps / 1e6:5.1f} MB/s"
            f"  {p.current:<40}", end="\r", flush=True))
        ok = up.run()
        print(f"\n{folder}: {'complete' if ok else 'INCOMPLETE - ' + up.progress.error}")
        if not ok:
            return 1
    return 0


def _cmd_ingest(args) -> int:
    from .ingest import Archive
    ar = Archive(args.archive)
    new = []
    for src in args.sources or []:
        new += ar.ingest_folder(src, club=args.club, evaluate=not args.no_evaluate,
                                stride=args.stride, move=args.move)
    if args.s3:
        from .recorder.upload import UploadConfig
        new += ar.ingest_s3(UploadConfig.from_code(args.s3), evaluate=not args.no_evaluate,
                            stride=args.stride)
    if args.reevaluate:
        for row in ar.db.execute("SELECT name FROM sessions WHERE evaluated_utc IS NULL").fetchall():
            ar.evaluate(row["name"], stride=args.stride)
    csv_path = ar.export_csv()
    n_review = len(ar.review_items())
    print(f"New sessions: {len(new)}" + (" - " + ", ".join(new) if new else ""))
    print(f"Catalogue: {csv_path}")
    print(f"Crops waiting for review: {n_review}  (ota review --archive {args.archive})")
    ar.close()
    return 0


def _cmd_review(args) -> int:
    from .review import main as review_main
    return review_main(args.archive)


def _cmd_dataset(args) -> int:
    from .ingest import Archive
    ar = Archive(args.archive)
    counts = ar.export_dataset(args.out, val_fraction=args.val)
    ar.close()
    print(json.dumps(counts, indent=2))
    print(f"Train with: python tools/train_classifier.py --data {args.out}")
    return 0


def _cmd_imu_check(args) -> int:
    """Live view of a WitMotion IMU: check the port, the rate and the axis directions."""
    import math
    from .sensors.feeds import LineFeed
    from .sensors.witmotion import WitMotionParser
    parser = WitMotionParser()
    state = {"n": 0, "last": None}

    def on_data(data: bytes, rx: float) -> None:
        for smp in parser.feed(data, rx):
            state["n"] += 1
            state["last"] = smp

    feed = LineFeed("IMU", args.port, on_data, binary=True).start()
    print("Tilt the camera nose UP: 'pitch' must become positive. Lower the RIGHT side: 'roll' "
          "positive. Ctrl+C to stop.\n")
    t0, n0 = time.time(), 0
    try:
        while True:
            time.sleep(0.5)
            s = state["last"]
            if s is None:
                print(f"  no data yet ({feed.error or 'waiting'})", end="\r")
                continue
            # our convention from the accelerometer (valid when the camera is still)
            pitch = math.degrees(math.atan2(s.ax, math.hypot(s.ay, s.az)))
            roll = math.degrees(math.atan2(s.ay, s.az))
            rate = (state["n"] - n0) / (time.time() - t0)
            t0, n0 = time.time(), state["n"]
            print(f"  pitch {pitch:+6.1f}  roll {roll:+6.1f}  |  gyro {s.gx:+7.1f} {s.gy:+7.1f} "
                  f"{s.gz:+7.1f} deg/s  |  {rate:5.0f} Hz  bad packets {parser.bad}   ", end="\r")
    except KeyboardInterrupt:
        pass
    feed.stop()
    print()
    return 0


def _cmd_probe(args) -> int:
    """Try resolutions and pixel formats and report what the camera really delivers."""
    import time
    import cv2
    from .sources import fourcc_str, open_usb_camera

    index = int(args.camera.split(":")[-1])
    modes = [(3840, 2160), (2560, 1440), (1920, 1080)]
    print(f"Probing {args.camera} ({args.frames} frames per mode)\n")
    print(f"{'requested':>22} | {'delivered':>11} {'format':>6} {'fps':>6}")
    print("-" * 52)
    best = None
    for fmt in ("YUY2", "MJPG"):
        for w, h in modes:
            try:
                cap = open_usb_camera(index, w, h, args.fps, fmt)
            except RuntimeError as e:
                print(e)
                return 1
            ok, img = cap.read()  # first frame can be slow
            t0 = time.perf_counter()
            n = 0
            while ok and n < args.frames:
                ok, img = cap.read()
                n += ok
            dt = time.perf_counter() - t0
            gw, gh = (img.shape[1], img.shape[0]) if img is not None else (0, 0)
            got = fourcc_str(cap.get(cv2.CAP_PROP_FOURCC)) or "?"
            fps = n / dt if dt > 0 else 0
            print(f"{fmt:>5} {w:>5}x{h:<5} @{args.fps:>3.0f} | {gw:>5}x{gh:<5} {got:>6} {fps:6.1f}")
            if img is not None and gw == 3840 and best is None:
                best = (fmt, img)
            cap.release()
    if best:
        out = Path(args.snapshot)
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), best[1])
        print(f"\n4K snapshot ({best[0]}) saved losslessly to {out}")
        print("Zoom into it: are single pixels sharp, or blocky (compression)?")
    else:
        print("\nNo 4K mode delivered.")
    print("\nIdeal: YUY2 3840x2160 with >= 10 fps. Set camera pixel_format in the config accordingly.")
    return 0


def _cmd_bench(args) -> int:
    """Measure processing speed on this machine with a synthetic 4K frame."""
    import os
    import time
    import cv2
    import numpy as np
    from .config import load_config
    from .detection import CandidateDetector, make_classifier
    from .synth import _draw_aircraft, _sky

    cfg = load_config(args.config)
    rng = np.random.default_rng(1)
    w, h = args.width, args.height
    img = _sky(w, h, int(h * 0.72), rng)
    for _ in range(5):
        _draw_aircraft(img, rng.uniform(0, w), rng.uniform(0, h * 0.6), rng.uniform(3, 12))
    img = np.clip(img + rng.normal(0, 2, img.shape), 0, 255).astype(np.uint8)

    def timeit(fn, n):
        fn()
        t = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t) / n * 1000

    print(f"Machine: {os.cpu_count()} logical CPUs, OpenCV {cv2.__version__}, "
          f"{cv2.getNumThreads()} OpenCV threads")
    print(f"Frame: {w}x{h}\n")
    rows = []
    variants = [("default", {}),
                ("full res only", {"pyramid_scales": [1.0]}),
                ("rect kernel", {"kernel_shape": "rect"}),
                ("no sky mask", {"sky_only": False})]
    for name, over in variants:
        d = cfg.detector.__class__(**{**cfg.detector.__dict__, **over})
        det = CandidateDetector(d, cfg.cameras[0])
        cands = det.detect(img)
        ms = timeit(lambda: det.detect(img), args.iterations)
        rows.append((name, ms, len(cands)))
    clf = make_classifier(cfg.classifier)
    det = CandidateDetector(cfg.detector, cfg.cameras[0])
    cands = det.detect(img)[:20]
    ms_clf = timeit(lambda: clf.score(img, [c for c in cands]), args.iterations)
    for name, ms, n in rows:
        print(f"  detector {name:<16} {ms:7.1f} ms   {1000 / ms:5.1f} fps   ({n} candidates)")
    print(f"  classifier ({clf.__class__.__name__}, {len(cands)} crops) {ms_clf:7.1f} ms")
    per_frame = rows[0][1] + ms_clf
    fps1 = 1000 / per_frame
    print(f"\nOne camera, default settings: ~{fps1:.1f} fps")
    cores = max(1, (os.cpu_count() or 2) // 2)
    for n in (1, 3):
        est = fps1 * min(1.0, cores / n) if n > 1 else fps1
        print(f"  {n} camera(s): ~{est:.1f} fps each (rough estimate, one process per camera)")
    print("\nTarget: >= 3-5 fps per camera at 4K.")
    return 0


def _cmd_cameras(args) -> int:
    import platform
    import cv2
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    found = False
    for i in range(args.max):
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f"usb:{i}  default {w}x{h}")
            found = True
        cap.release()
    if not found:
        print("No cameras found.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ota", description="Optical Traffic Alert")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="create a synthetic test video")
    s.add_argument("out", nargs="?", default="data/synthetic/test.mp4")
    s.add_argument("--width", type=int, default=1920)
    s.add_argument("--height", type=int, default=1080)
    s.add_argument("--fps", type=float, default=30.0)
    s.add_argument("--duration", type=float, default=30.0)
    s.set_defaults(func=_cmd_synth)

    r = sub.add_parser("run", help="run the detection pipeline")
    r.add_argument("--config", default="config/default.yaml")
    r.add_argument("--aircraft", help="aircraft profile name or YAML path (e.g. v35a)")
    r.add_argument("--video", help="process a video file instead of the configured cameras")
    r.add_argument("--hfov", type=float, help="horizontal FOV of the video's camera (deg)")
    r.add_argument("--show", action="store_true", help="show annotated frames (q to quit)")
    r.add_argument("--record", help="write annotated video to this path")
    r.add_argument("--audio", choices=["print", "pyttsx3", "piper"])
    r.add_argument("--language", choices=["en", "de"])
    r.add_argument("--realtime", action="store_true", help="play video files at their real speed")
    r.add_argument("--stride", type=int, help="process every n-th frame")
    r.add_argument("--max-frames", type=int)
    r.set_defaults(func=_cmd_run)

    c = sub.add_parser("record", help="record raw camera footage")
    c.add_argument("--camera", default="usb:0")
    c.add_argument("--out", default="data/raw/recording.avi")
    c.add_argument("--width", type=int, default=3840)
    c.add_argument("--height", type=int, default=2160)
    c.add_argument("--fps", type=float, default=15.0)
    c.add_argument("--format", default="YUY2", help="camera pixel format: YUY2 | MJPG | ''")
    c.add_argument("--codec", choices=sorted(CODECS), default="mjpg",
                   help="mjpg = near-lossless (default), ffv1 = lossless, mp4v = small")
    c.add_argument("--exposure", type=float, help="manual exposure (driver units)")
    c.add_argument("--gain", type=float)
    c.add_argument("--seconds", type=float, default=0)
    c.set_defaults(func=_cmd_record)

    se = sub.add_parser("session", help="record camera + ADS-B + FLARM/OGN into a session folder")
    se.add_argument("--settings", help="settings JSON (as saved by ota-recorder)")
    se.add_argument("--out", help="parent folder for sessions")
    se.add_argument("--camera", help="usb:<index> or file:<video>")
    se.add_argument("--no-camera", action="store_true", help="traffic only")
    se.add_argument("--station")
    se.add_argument("--lat", type=float)
    se.add_argument("--lon", type=float)
    se.add_argument("--elev", type=float, help="camera altitude above MSL (m)")
    se.add_argument("--heading", type=float, help="true direction the camera looks at (deg)")
    se.add_argument("--pitch", type=float)
    se.add_argument("--hfov", type=float)
    se.add_argument("--width", type=int)
    se.add_argument("--height", type=int)
    se.add_argument("--fps", type=float)
    se.add_argument("--format")
    se.add_argument("--adsb", help="dump1090 SBS address host:port ('' = off)")
    se.add_argument("--ogn-online", action=argparse.BooleanOptionalAction, default=None)
    se.add_argument("--ogn-radius", type=float)
    se.add_argument("--ogn-local", help="local OGN receiver host:port (ogn-decode, port 50001)")
    se.add_argument("--flarm", help="FLARM NMEA port, e.g. COM4@19200 or host:2000")
    se.add_argument("--gps", help="GPS receiver NMEA port for the own position, e.g. COM5@9600")
    se.add_argument("--mode", choices=["ground", "aircraft"])
    se.add_argument("--imu", help="WitMotion attitude sensor port, e.g. COM6@115200")
    se.add_argument("--own-icao", help="own transponder address (hex), excluded from traffic")
    se.add_argument("--record-mode", choices=["smart", "all"])
    se.add_argument("--qnh", type=float)
    se.add_argument("--operator")
    se.add_argument("--notes")
    se.add_argument("--seconds", type=float, default=0)
    se.set_defaults(func=_cmd_session)

    ev = sub.add_parser("evaluate", help="compare a session's video with ADS-B/FLARM positions")
    ev.add_argument("session", help="session folder (contains session.json)")
    ev.add_argument("--config", default="config/default.yaml")
    ev.add_argument("--stride", type=int, default=1, help="process every n-th frame")
    ev.add_argument("--gate", type=float, default=1.0, help="match tolerance in degrees")
    ev.add_argument("--no-calibrate", action="store_true",
                    help="do not estimate the camera heading/pitch correction")
    ev.add_argument("--overlay", action="store_true", help="also write eval/overlay.mp4")
    ev.add_argument("--max-frames", type=int)
    ev.set_defaults(func=_cmd_evaluate)

    ss = sub.add_parser("synth-session", help="create a synthetic recording session")
    ss.add_argument("out", nargs="?", default="data/sessions")
    ss.set_defaults(func=_cmd_synth_session)

    uc = sub.add_parser("upload-code", help="create an upload code for club members (B2/R2/S3)")
    uc.add_argument("--endpoint", required=True,
                    help="S3 endpoint, e.g. https://s3.eu-central-003.backblazeb2.com")
    uc.add_argument("--bucket", required=True)
    uc.add_argument("--key-id", required=True)
    uc.add_argument("--secret", required=True)
    uc.add_argument("--region", default="auto")
    uc.add_argument("--prefix", default="", help="folder in the bucket, e.g. the club name")
    uc.add_argument("--check", action="store_true", help="test the key with a small upload first")
    uc.set_defaults(func=_cmd_upload_code)

    up = sub.add_parser("upload", help="upload session folders with an upload code (resumable)")
    up.add_argument("sessions", nargs="+")
    up.add_argument("--code", help="upload code (or environment variable OTA_UPLOAD_CODE)")
    up.set_defaults(func=_cmd_upload)

    ig = sub.add_parser("ingest", help="import sessions (folders or S3), evaluate, catalogue")
    ig.add_argument("sources", nargs="*", help="folders to scan for sessions (USB stick, cloud folder)")
    ig.add_argument("--s3", help="ota1: code of a key that may READ the bucket")
    ig.add_argument("--archive", default="data/archive")
    ig.add_argument("--club", default="local", help="club/source name for folder imports")
    ig.add_argument("--move", action="store_true", help="move instead of copy (folder imports)")
    ig.add_argument("--no-evaluate", action="store_true")
    ig.add_argument("--reevaluate", action="store_true", help="also evaluate older, unevaluated sessions")
    ig.add_argument("--stride", type=int, default=1, help="evaluate every n-th frame (faster)")
    ig.set_defaults(func=_cmd_ingest)

    rv = sub.add_parser("review", help="sort crops without cooperative traffic by key press")
    rv.add_argument("--archive", default="data/archive")
    rv.set_defaults(func=_cmd_review)

    ds = sub.add_parser("dataset", help="export the labelled crops as train/val (split by session)")
    ds.add_argument("--archive", default="data/archive")
    ds.add_argument("--out", default="data/crops_own")
    ds.add_argument("--val", type=float, default=0.2)
    ds.set_defaults(func=_cmd_dataset)

    ic = sub.add_parser("imu-check", help="live view of the attitude sensor (axes, rate)")
    ic.add_argument("--port", default="COM6@115200")
    ic.set_defaults(func=_cmd_imu_check)

    pr = sub.add_parser("probe", help="test which resolutions/formats/fps a camera really delivers")
    pr.add_argument("--camera", default="usb:0")
    pr.add_argument("--fps", type=float, default=30.0)
    pr.add_argument("--frames", type=int, default=30)
    pr.add_argument("--snapshot", default="data/raw/probe_4k.png")
    pr.set_defaults(func=_cmd_probe)

    b = sub.add_parser("bench", help="measure processing speed on this machine")
    b.add_argument("--config", default="config/default.yaml")
    b.add_argument("--width", type=int, default=3840)
    b.add_argument("--height", type=int, default=2160)
    b.add_argument("--iterations", type=int, default=5)
    b.set_defaults(func=_cmd_bench)

    k = sub.add_parser("cameras", help="list cameras")
    k.add_argument("--max", type=int, default=6)
    k.set_defaults(func=_cmd_cameras)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
