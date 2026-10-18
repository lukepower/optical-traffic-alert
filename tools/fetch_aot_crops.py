"""Download a sample of the AOT dataset and cut classifier crops on the fly.

Unlike ``extract_aot_crops.py`` (which works on a full local copy), this
script never stores the 2448x2048 frames: each selected frame is downloaded
over plain HTTPS from the public S3 bucket, cropped in memory and discarded.
A few thousand frames therefore need only a few hundred MB of disk.

Selection (from ``ImageSets/groundtruth.csv``):

* per flight up to ``--aircraft-frames`` frames that contain an Airplane,
  Helicopter or Drone, spread evenly over the flight
* per flight up to ``--bird-frames`` frames that contain a Bird or Flock
* ``Airborne`` (unidentified object) is skipped - it mixes aircraft and birds
* train/val split by flight (``--val-fraction``), never the same flight in both
* the selection is cached in ``<out>/selection.json`` (delete it to re-select)

Output (same layout as extract_aot_crops.py, Ultralytics classification format)::

    <out>/train/aircraft  <out>/train/bird  <out>/train/background
    <out>/val/...

The script is resumable and time-boxed: it skips frames already done
(``<out>/done.txt``) and stops cleanly after ``--max-seconds``. Run it again
until it reports nothing left to do.

    python tools/fetch_aot_crops.py --gt data/aot/part1/ImageSets/groundtruth.csv --out data/crops
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np

BUCKET = "https://airborne-obj-detection-challenge-training.s3.amazonaws.com"
CLASS_MAP = {"airplane": "aircraft", "helicopter": "aircraft", "drone": "aircraft",
             "bird": "bird", "flock": "bird"}


def object_class(obj_id: str) -> str | None:
    return CLASS_MAP.get(re.sub(r"\d+", "", obj_id).lower())


def is_val(flight: str, fraction: float) -> bool:
    h = int(hashlib.md5(flight.encode()).hexdigest()[:8], 16)
    return (h % 1000) / 1000.0 < fraction


def load_frames(gt_csv: Path) -> dict[str, dict]:
    """img_name -> {flight, objects:[(cls, l, t, r, b)], all_boxes:[...]}"""
    frames: dict[str, dict] = {}
    with open(gt_csv, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("id"):
                continue
            fr = frames.setdefault(row["img_name"], {"flight": row["flight_id"],
                                                     "frame": int(row["frame"]),
                                                     "objects": [], "all_boxes": []})
            box = tuple(float(row[k]) for k in ("gt_left", "gt_top", "gt_right", "gt_bottom"))
            fr["all_boxes"].append(box)
            cls = object_class(row["id"])
            if cls:
                fr["objects"].append((cls, *box, row["id"]))
    return frames


def select(frames: dict[str, dict], n_air: int, n_bird: int, seed: int) -> list[str]:
    by_flight: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for name, fr in frames.items():
        classes = {o[0] for o in fr["objects"]}
        for c in classes:
            by_flight[fr["flight"]][c].append((fr["frame"], name))
    chosen: set[str] = set()
    for flight, per_cls in by_flight.items():
        for cls, n in (("aircraft", n_air), ("bird", n_bird)):
            items = sorted(per_cls.get(cls, []))
            if not items:
                continue
            idx = np.linspace(0, len(items) - 1, min(n, len(items))).astype(int)
            chosen.update(items[i][1] for i in idx)
    out = sorted(chosen)
    random.Random(seed).shuffle(out)
    return out


def square_crop(img: np.ndarray, cx: float, cy: float, side: int) -> np.ndarray:
    half = side // 2
    pad = cv2.copyMakeBorder(img, half, half, half, half, cv2.BORDER_REFLECT)
    x0, y0 = int(round(cx)), int(round(cy))
    return pad[y0:y0 + side, x0:x0 + side]


def process(name: str, fr: dict, out: Path, args, rng: random.Random) -> int:
    url = f"{BUCKET}/part1/Images/{fr['flight']}/{name}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = np.frombuffer(resp.read(), np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return 0
    split = "val" if is_val(fr["flight"], args.val_fraction) else "train"
    stem = Path(name).stem
    n = 0
    for cls, l, t, r, b, oid in fr["objects"]:
        w, h = r - l, b - t
        side = max(args.crop, int(max(w, h) * args.context))
        crop = cv2.resize(square_crop(img, (l + r) / 2, (t + b) / 2, side), (args.crop, args.crop),
                          interpolation=cv2.INTER_AREA)
        dst = out / split / cls / f"{stem}_{oid}.png"
        dst.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dst), crop)
        n += 1
    # background crops: random spots away from every labelled object
    H, W = img.shape[:2]
    for k in range(args.background_per_frame):
        for _ in range(30):
            bx, by = rng.uniform(32, W - 32), rng.uniform(32, H - 32)
            if all(not (l - 64 < bx < r + 64 and t - 64 < by < b + 64) for l, t, r, b in fr["all_boxes"]):
                break
        else:
            continue
        dst = out / split / "background" / f"{stem}_bg{k}.png"
        dst.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dst), square_crop(img, bx, by, args.crop))
        n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", required=True, help="path to part1/ImageSets/groundtruth.csv")
    ap.add_argument("--out", default="data/crops")
    ap.add_argument("--aircraft-frames", type=int, default=6, help="frames per flight with aircraft")
    ap.add_argument("--bird-frames", type=int, default=20, help="frames per flight with birds")
    ap.add_argument("--background-per-frame", type=int, default=1)
    ap.add_argument("--crop", type=int, default=64)
    ap.add_argument("--context", type=float, default=2.5)
    ap.add_argument("--val-fraction", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-seconds", type=float, default=0, help="stop after this time (0 = no limit)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    done_file = out / "done.txt"
    done = set(done_file.read_text().split()) if done_file.exists() else set()

    # Parsing the 300 MB ground truth takes a while; cache the selection.
    cache = out / "selection.json"
    if cache.exists():
        frames = json.loads(cache.read_text())
    else:
        all_frames = load_frames(Path(args.gt))
        chosen = select(all_frames, args.aircraft_frames, args.bird_frames, args.seed)
        frames = {n: all_frames[n] for n in chosen}
        cache.write_text(json.dumps(frames))
    todo = [n for n in frames if n not in done]
    print(f"{len(frames)} selected frames, {len(done)} done, {len(todo)} to fetch", flush=True)

    t0 = time.monotonic()
    lock = threading.Lock()
    crops = errors = finished = 0
    rng = random.Random(args.seed)
    with ThreadPoolExecutor(args.workers) as ex, open(done_file, "a") as df:
        futures = {}
        it = iter(todo)
        # keep a bounded number of jobs in flight so a time limit stops quickly
        for name in it:
            futures[ex.submit(process, name, frames[name], out, args, random.Random(rng.random()))] = name
            if len(futures) >= args.workers * 2:
                break
        while futures:
            for fut in as_completed(list(futures)):
                name = futures.pop(fut)
                try:
                    c = fut.result()
                    with lock:
                        crops += c
                        finished += 1
                        df.write(name + "\n")
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    print(f"error {name}: {e}", flush=True)
                if not args.max_seconds or time.monotonic() - t0 < args.max_seconds:
                    nxt = next(it, None)
                    if nxt:
                        futures[ex.submit(process, nxt, frames[nxt], out, args,
                                          random.Random(rng.random()))] = nxt
                break
    left = len(todo) - finished
    print(f"fetched {finished} frames -> {crops} crops, {errors} errors, "
          f"{time.monotonic() - t0:.0f}s, {left} frames left", flush=True)
    for split in ("train", "val"):
        for cls in ("aircraft", "bird", "background"):
            d = out / split / cls
            print(f"  {split}/{cls}: {len(list(d.glob('*.png'))) if d.exists() else 0}")


if __name__ == "__main__":
    main()
