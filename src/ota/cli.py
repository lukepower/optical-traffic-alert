"""Command line interface.

    ota synth  out/test.mp4                 # create a synthetic test video
    ota run    --video out/test.mp4 --show  # run the pipeline on a video
    ota run    --config config/default.yaml --aircraft v35a   # live cameras
    ota record --camera usb:0 --out data/raw/flight1.mp4      # record raw footage
    ota cameras                             # list USB cameras that OpenCV can open
"""

from __future__ import annotations

import argparse
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


def _cmd_record(args) -> int:
    import cv2
    from .config import CameraConfig
    from .sources import open_source

    cam = CameraConfig(name="rec", source=args.camera, width=args.width, height=args.height,
                       fps=args.fps)
    src = open_source(cam)
    w, h = src.size
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (w, h))
    print(f"Recording {w}x{h} from {args.camera} to {args.out} - press Ctrl+C to stop")
    n = 0
    try:
        for fr in src.frames():
            writer.write(fr.image)
            n += 1
            if args.seconds and fr.t >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        writer.release()
        src.close()
    print(f"{n} frames written")
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
    c.add_argument("--out", default="data/raw/recording.mp4")
    c.add_argument("--width", type=int, default=3840)
    c.add_argument("--height", type=int, default=2160)
    c.add_argument("--fps", type=float, default=30.0)
    c.add_argument("--seconds", type=float, default=0)
    c.set_defaults(func=_cmd_record)

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
