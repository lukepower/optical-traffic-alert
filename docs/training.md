# Klassifikator trainieren

Der Klassifikator (Stufe 2) entscheidet für jeden Kandidaten-Ausschnitt: **Flugzeug**, **Vogel**
oder **Hintergrund**. Trainiert wird auf einem Rechner mit NVIDIA-GPU (z. B. 24 GB VRAM); der Laptop
führt das fertige Modell nur aus.

## Überblick

```
AOT-Datensatz (S3) ──► extract_aot_crops.py ──► data/crops/{train,val}/{aircraft,background,bird}
                                                         │
                                       train_classifier.py (Ultralytics, GPU)
                                                         │
                                  models/classifier.xml + .bin (OpenVINO) ──► Laptop
```

## 1. Daten: Airborne Object Tracking (AOT)

Der AOT-Datensatz von Amazon Prime Air liegt frei im AWS Open Data Registry
([registry.opendata.aws/airborne-object-tracking](https://registry.opendata.aws/airborne-object-tracking)).
Er enthält echte Luft-zu-Luft-Flüge mit annotierten Begegnungen: Flugzeuge, Helikopter, Vögel,
Drohnen – viele davon nur wenige Pixel groß, also genau unser Problem.

Der komplette Datensatz ist mehrere Terabyte groß. **Nicht alles laden** – für den Klassifikator
reichen einige Dutzend Flüge.

```powershell
# AWS CLI installieren, dann ohne Login (öffentlicher Bucket):
aws s3 cp --no-sign-request s3://airborne-obj-detection-challenge-training/part1/ImageSets/groundtruth.json D:\aot\part1\ImageSets\
# einzelne Flüge (Ordnernamen aus groundtruth.json bzw. per "aws s3 ls"):
aws s3 sync --no-sign-request s3://airborne-obj-detection-challenge-training/part1/Images/<flight_id> D:\aot\part1\Images\<flight_id>
```

Ground-Truth-Felder, die das Skript nutzt (pro annotiertem Objekt): `img_name`, `flight_id`,
`id` (z. B. `Airplane1`, `Bird3`), `bb` = `[links, oben, breite, höhe]`, `labels.is_above_horizon`.
Das Skript sucht diese Einträge tolerant im JSON, unabhängig von der genauen Verschachtelung.

## 2. Ausschnitte erzeugen

```powershell
python tools\extract_aot_crops.py --aot D:\aot\part1 --out data\crops --max-per-flight 400
```

* Flugzeug, Helikopter, Drohne → `aircraft`; Vogel, Schwarm → `bird`
* pro Objekt ein zufälliger Hintergrund-Ausschnitt aus demselben Bild → `background`
* Aufteilung in train/val **nach Flügen** – kein Flug landet in beiden Teilen
* Ausschnittgröße 64 px (= `classifier.crop_px`), bei großen Objekten mit mehr Kontext und
  herunterskaliert

**Eigene Daten** lassen sich einfach dazulegen: Ausschnitte aus eigenen Videos
(siehe [datensammlung.md](datensammlung.md)) als PNG in die passenden Ordner kopieren. Besonders
wertvoll sind **Fehlkandidaten** aus eigenen Aufnahmen (Scheibenreflexe, Wolkenränder, Gelände) als
`background` – genau die kommen im Flugzeug vor.

## 3. Trainieren (GPU-Rechner)

```bash
pip install -e .[train]
python tools/train_classifier.py --data data/crops --epochs 40 --device 0
```

* Modell: `yolo11n-cls` (Nano, Klassifikation), Eingabe 64×64
* Augmentierung bewusst mild (kleine Objekte vertragen keine starken Zuschnitte)
* Ergebnis: `models/classifier.xml` und `models/classifier.bin`
* Prüfen, dass Klasse 0 `aircraft` ist (das Skript warnt sonst)

## 4. Auf dem Laptop einsetzen

`models/classifier.xml` und `.bin` auf den Laptop kopieren, dann in `config/default.yaml`:

```yaml
classifier:
  backend: openvino
  model_path: models/classifier.xml
  device: AUTO        # oder GPU für die Iris Xe
  min_probability: 0.5
```

## 5. Bewerten

* Ultralytics gibt Genauigkeit und Konfusionsmatrix auf `val` aus (`runs/classifier/train`).
* Wichtiger ist die Wirkung in der Pipeline: Mit und ohne Klassifikator dieselben Videos laufen
  lassen und `events.jsonl` vergleichen – Anzahl `TRAFFIC`-Ansagen für Nicht-Flugzeuge muss sinken,
  echte Ziele dürfen nicht verloren gehen.
* `min_probability` lieber niedrig ansetzen (0,3–0,5): Der Tracker filtert zusätzlich. Ein
  verworfenes echtes Flugzeug ist schlimmer als ein durchgelassener Vogel.

## Ausblick

* Jetson: dasselbe Modell nach ONNX → TensorRT exportieren (`format="engine"`).
* Temporaler Klassifikator: mehrere Ausschnitte desselben Tracks gemeinsam bewerten
  (Flugzeuge bewegen sich gleichmäßig, Vögel flattern).
