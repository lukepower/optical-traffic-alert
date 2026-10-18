"""Train the crop classifier and export it for OpenVINO.

Run this on the machine with the GPU (not on the laptop):

    pip install -e .[train]
    python tools/train_classifier.py --data data/crops --epochs 40

The result is copied to ``models/classifier.xml`` + ``models/classifier.bin``.
Copy both files to the laptop and set ``classifier.backend: openvino``.

Uses an Ultralytics YOLO classification model ("-cls"). At 64x64 px input the
nano model is tiny and runs comfortably on an Intel Iris Xe via OpenVINO.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/crops", help="folder with train/ and val/")
    ap.add_argument("--model", default="yolo11n-cls.pt", help="pretrained classification model")
    ap.add_argument("--imgsz", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--device", default="0", help="GPU index, or 'cpu'")
    ap.add_argument("--out", default="models")
    args = ap.parse_args()

    from ultralytics import YOLO  # pip install -e .[train]

    model = YOLO(args.model)
    model.train(data=args.data, imgsz=args.imgsz, epochs=args.epochs, batch=args.batch,
                device=args.device, project="runs/classifier", name="train", exist_ok=True,
                # small objects: keep augmentations mild, no heavy crops
                fliplr=0.5, flipud=0.0, hsv_v=0.3, degrees=10, scale=0.2, erasing=0.0)
    names = model.names
    print("classes:", names)
    if names.get(0) != "aircraft":
        print("WARNING: class 0 is not 'aircraft' - set classifier.aircraft_class accordingly")

    export_dir = Path(model.export(format="openvino", imgsz=args.imgsz, half=False))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    xml = next(export_dir.glob("*.xml"))
    shutil.copy(xml, out / "classifier.xml")
    shutil.copy(xml.with_suffix(".bin"), out / "classifier.bin")
    print(f"OpenVINO model written to {out / 'classifier.xml'}")


if __name__ == "__main__":
    main()
