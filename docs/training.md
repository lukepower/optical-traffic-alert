# Training the classifier

The classifier (stage 2) decides for each candidate crop: **aircraft**, **bird** or **background**.
Training runs on a desktop with a GPU (NVIDIA or AMD Radeon); the laptop only runs the finished model.

## Overview

```
AOT dataset (public S3) ──► fetch_aot_crops.py ──► data/crops/{train,val}/{aircraft,background,bird}
                                                          │
                                        train_classifier.py (Ultralytics, GPU)
                                                          │
                                   models/classifier.xml + .bin (OpenVINO) ──► laptop
```

## 1. Data: Airborne Object Tracking (AOT)

The AOT dataset by Amazon Prime Air is freely available in the AWS Open Data Registry
([registry.opendata.aws/airborne-object-tracking](https://registry.opendata.aws/airborne-object-tracking)).
It contains real air-to-air flights with annotated encounters: airplanes, helicopters, birds,
drones – many of them only a few pixels in size, which is exactly our problem. Frames are
2448×2048 grayscale PNGs.

The full dataset is several terabytes. **Do not download it all.** `tools/fetch_aot_crops.py`
downloads only selected frames over plain HTTPS, cuts the crops in memory and discards the frame:

```powershell
# 1. ground truth (~300 MB CSV)
curl -o data\aot\part1\ImageSets\groundtruth.csv https://airborne-obj-detection-challenge-training.s3.amazonaws.com/part1/ImageSets/groundtruth.csv

# 2. crops (resumable; run again until "0 frames left")
python tools\fetch_aot_crops.py --gt data\aot\part1\ImageSets\groundtruth.csv --out data\crops --workers 32
```

Default selection: per flight up to 6 frames with aircraft and up to 20 frames with birds, plus one
background crop per frame. With part1 (1,311 flights) this is about 15,500 frames and roughly
50,000 crops:

| Split | aircraft | bird | background |
|---|---|---|---|
| train | 10,198 | 9,709 | 13,218 |
| val | 1,690 | 1,607 | 2,284 |

Mapping: Airplane, Helicopter, Drone → `aircraft`; Bird, Flock → `bird`. `Airborne`
(unidentified objects) is skipped because it mixes both. Train/val are split **by flight** – no
flight appears in both.

Ground-truth fields used (one row per labelled object): `flight_id`, `img_name`, `frame`, `id`
(e.g. `Airplane1`, `Bird3`), `gt_left/top/right/bottom`.

If you already have a local copy of the dataset (`aws s3 sync --no-sign-request ...`), use
`tools/extract_aot_crops.py` instead – it reads `groundtruth.json` and local images.

**Own data** can simply be added: crops from your own videos (see
[data-collection.md](data-collection.md)) as PNG into the matching folders. **False candidates** from
your own recordings (windscreen reflections, cloud edges, terrain) are particularly valuable as
`background` – exactly what occurs in the aircraft.

## 2. Training (GPU machine)

### One click on Windows

Double-click `tools\train_windows.bat`. It

1. creates a separate virtual environment `.venv-train`,
2. installs PyTorch with CUDA, Ultralytics, OpenVINO and the project,
3. checks that the GPU is visible,
4. waits until `data\crops\READY` exists (so it can be started while crops are still downloading),
5. trains and exports `models\classifier.xml/.bin`.

Everything is logged to `data\train_windows.log`. Options: `tools\train_windows.bat -Epochs 60`,
`-Gpu nvidia|amd|cpu` (default: auto-detect), `-AllowCpu` (fall back to CPU training if no GPU works).

**GPU backends**

| GPU | PyTorch build | Requirements |
|---|---|---|
| NVIDIA | CUDA 12.6 from download.pytorch.org | current NVIDIA driver |
| AMD Radeon RX 7000 / 9000 | ROCm 7.2.1 for Windows from repo.radeon.com | Python 3.12, AMD Software: Adrenalin Edition 26.2.2 or newer |
| none | CPU | slower; fine for the small 64×64 classifier (roughly an hour) |

ROCm exposes the Radeon card through PyTorch's `cuda` API, so Ultralytics uses `--device 0` for both
vendors. AMD's Windows support matrix lists the RDNA 3 (gfx110x) and RDNA 4 cards; see
[AMD's documentation](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html).

### Manually

```bash
pip install -e .[train]
python tools/train_classifier.py --data data/crops --epochs 40 --device 0
```

* Model: `yolo11n-cls` (nano, classification), input 64×64
* Augmentation deliberately mild (small objects don't tolerate heavy cropping)
* Result: `models/classifier.xml` and `models/classifier.bin`
* Check that class 0 is `aircraft` (the script warns otherwise)

## 3. Using it on the laptop

Copy `models/classifier.xml` and `.bin` to the laptop, then in `config/default.yaml`:

```yaml
classifier:
  backend: auto       # uses the model when models/classifier.xml exists
  model_path: models/classifier.xml
  device: AUTO        # or GPU for the Iris Xe
  min_probability: 0.3
```

## 4. Evaluation

### First model (2026-10-18)

Trained on the AOT part1 sample above, `yolo11n-cls`, 64×64, 40 epochs on an AMD Radeon RX 7700 XT
(ROCm 7.2.1, Windows, MIOpen disabled; ~1 min 20 s per epoch). Validation top-1 accuracy 82.7 %.
Validation loss started to rise in the last epochs (mild overfitting) – more and more varied data
will help more than more epochs.

Confusion matrix on the validation flights (OpenVINO model, rows = truth):

| truth \ predicted | aircraft | background | bird |
|---|---|---|---|
| aircraft (1,690) | **1,313** | 107 | 270 |
| background (2,284) | 55 | **2,183** | 46 |
| bird (1,607) | 323 | 179 | **1,105** |

Effect of the threshold on the aircraft probability (`classifier.min_probability`):

| threshold | aircraft kept | background passed | birds passed |
|---|---|---|---|
| 0.2 | 87 % | 4.9 % | 38 % |
| **0.3 (default)** | **83 %** | **3.5 %** | **30 %** |
| 0.5 | 77 % | 2.2 % | 19 % |
| 0.7 | 69 % | 1.3 % | 12 % |

Reading these numbers:

* Per **frame**, 17 % of aircraft crops are dropped at 0.3. A **track** still survives, because it
  only needs `min_hits` detections and tolerates `max_misses` gaps.
* Aircraft vs. **bird** is the weak spot: at 5–10 px they are hard to tell apart even for a human.
  A temporal classifier (motion pattern over the track) is the planned fix.
* Background crops in AOT are random patches. Real false candidates (horizon, cloud edges,
  windscreen reflections) are harder – own recordings are needed to measure that.
* In the synthetic end-to-end test the pipeline gives the same callouts with and without the
  classifier, and is faster with it (39 vs. 52 ms/frame) because fewer candidates reach the tracker.

### How to evaluate a new model

* Ultralytics reports accuracy and a confusion matrix on `val` (`runs/classifier/train`).
* More important is the effect in the pipeline: run the same videos with and without the classifier
  and compare `events.jsonl` – `TRAFFIC` callouts for non-aircraft must drop, real targets must not
  be lost.
* Keep `min_probability` rather low (0.3–0.5): the tracker filters as well. A rejected real aircraft
  is worse than a bird that gets through.

### Deployment

`models/` is not in the repository. Copy `classifier.xml` + `classifier.bin` into `models/` on the
machine that runs the pipeline. With the default `classifier.backend: auto` the model is used
automatically when it is present and OpenVINO is installed.

## Outlook

* Jetson: export the same model to ONNX → TensorRT (`format="engine"`).
* Temporal classifier: rate several crops of the same track together (aircraft move smoothly,
  birds flap).
* AOT is mostly flat-country, partly hazy imagery. Own recordings (mountains, haze, sun) will be
  needed for good performance in alpine terrain.
