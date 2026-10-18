"""Build a crop-classification dataset from the Airborne Object Tracking (AOT) dataset.

AOT (Amazon Prime Air, AWS Open Data) contains real air-to-air flights with
labelled encounters. Ground truth lives in ``ImageSets/groundtruth.json``;
every labelled object instance has (among others):

    img_name   image file name (inside Images/<flight_id>/)
    flight_id  flight
    id         object id such as "Airplane1", "Helicopter2", "Bird3", "Drone1"
    bb         [left, top, width, height] in pixels
    labels     {"is_above_horizon": 1 | 0 | -1}

This script walks the JSON (tolerant to the exact nesting), cuts square crops
around each object and around random background spots, and writes:

    <out>/train/aircraft/*.png   Airplane, Helicopter, Drone
    <out>/train/bird/*.png       Bird, Flock
    <out>/train/background/*.png random sky/terrain patches without objects
    <out>/val/...                same, split by flight (no flight in both sets)

Folder names are sorted alphabetically by Ultralytics, so "aircraft" becomes
class 0 - matching ``classifier.aircraft_class: 0`` in config/default.yaml.

Usage (after downloading some flights, see docs/training.md):

    python tools/extract_aot_crops.py --aot D:/aot/part1 --out data/crops --max-per-flight 400
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

CLASS_MAP = {"airplane": "aircraft", "helicopter": "aircraft", "drone": "aircraft",
             "bird": "bird", "flock": "bird"}


def iter_entities(node):
    """Yield every dict that looks like an AOT object instance."""
    if isinstance(node, dict):
        if "bb" in node and "img_name" in node:
            yield node
        else:
            for v in node.values():
                yield from iter_entities(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_entities(v)


def object_class(obj_id: str) -> str | None:
    base = re.sub(r"\d+", "", str(obj_id)).lower()
    return CLASS_MAP.get(base)


def square_crop(img: np.ndarray, cx: float, cy: float, size: int) -> np.ndarray:
    half = size // 2
    pad = cv2.copyMakeBorder(img, half, half, half, half, cv2.BORDER_REFLECT)
    x0, y0 = int(round(cx)), int(round(cy))
    return pad[y0:y0 + size, x0:x0 + size]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aot", required=True, help="AOT root (folder with ImageSets/ and Images/)")
    ap.add_argument("--out", default="data/crops")
    ap.add_argument("--crop", type=int, default=64, help="crop size in px (= classifier.crop_px)")
    ap.add_argument("--context", type=float, default=2.5,
                    help="crop covers this many times the object size, at least --crop px")
    ap.add_argument("--max-per-flight", type=int, default=300)
    ap.add_argument("--background-ratio", type=float, default=1.0,
                    help="background crops per object crop")
    ap.add_argument("--val-fraction", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    root = Path(args.aot)
    gt = json.loads((root / "ImageSets" / "groundtruth.json").read_text())
    rng = random.Random(args.seed)

    by_flight: dict[str, list[dict]] = defaultdict(list)
    for e in iter_entities(gt):
        cls = object_class(e.get("id", ""))
        if cls and e.get("flight_id"):
            by_flight[e["flight_id"]].append({**e, "cls": cls})

    flights = sorted(by_flight)
    rng.shuffle(flights)
    n_val = max(1, int(len(flights) * args.val_fraction)) if len(flights) > 1 else 0
    val_flights = set(flights[:n_val])
    print(f"{sum(map(len, by_flight.values()))} labelled objects in {len(flights)} flights "
          f"({n_val} for validation)")

    out = Path(args.out)
    counts: dict[str, int] = defaultdict(int)
    for flight in flights:
        split = "val" if flight in val_flights else "train"
        items = by_flight[flight]
        rng.shuffle(items)
        for e in items[: args.max_per_flight]:
            img_path = root / "Images" / flight / e["img_name"]
            if not img_path.exists():
                continue  # flight only partially downloaded
            img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            if img is None:
                continue
            left, top, w, h = e["bb"]
            cx, cy = left + w / 2, top + h / 2
            side = max(args.crop, int(max(w, h) * args.context))
            crop = cv2.resize(square_crop(img, cx, cy, side), (args.crop, args.crop),
                              interpolation=cv2.INTER_AREA)
            name = f"{flight}_{Path(e['img_name']).stem}_{e['id']}.png"
            dst = out / split / e["cls"] / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(dst), crop)
            counts[f"{split}/{e['cls']}"] += 1

            # background crops from the same frame, away from the object
            n_bg = int(args.background_ratio) + (rng.random() < args.background_ratio % 1)
            H, W = img.shape[:2]
            for k in range(n_bg):
                for _ in range(20):
                    bx, by = rng.uniform(0, W), rng.uniform(0, H)
                    if abs(bx - cx) > 3 * side or abs(by - cy) > 3 * side:
                        break
                bg = square_crop(img, bx, by, args.crop)
                dst = out / split / "background" / f"{flight}_{Path(e['img_name']).stem}_bg{k}.png"
                dst.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(dst), bg)
                counts[f"{split}/background"] += 1

    for k in sorted(counts):
        print(f"{k:22s} {counts[k]}")


if __name__ == "__main__":
    main()
