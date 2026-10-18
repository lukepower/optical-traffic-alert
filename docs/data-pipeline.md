# Data pipeline: from club recordings to training data

```
members (OTA Recorder)                project lead
───────────────────────               ────────────────────────────────────────────────────
record ─► Hand in ─► upload (B2/R2) ─┐
                 └─► USB / cloud ─────┼─► ota ingest ─► archive + catalogue
                                      │        │
                                      │        ├─► ota evaluate (automatic): range, accuracy
                                      │        └─► crops: dataset/aircraft (auto-labelled)
                                      │                   dataset/review  (no cooperative aircraft)
                                      │
                                      └──────────────► ota review  ─► aircraft / bird / background
                                                       ota dataset ─► train/val ─► train_classifier
```

## 1. Import: `ota ingest`

```powershell
# from a USB stick or a synced cloud folder (every session folder found below is imported)
ota ingest E:\ --club aeroclub-bz

# from the upload bucket - needs a key that may READ (not the members' write-only key)
ota upload-code --endpoint https://s3.eu-central-003.backblazeb2.com --region eu-central-003 `
    --bucket ota-recordings --key-id <read keyID> --secret <read key>   # prints ota1:...
ota ingest --s3 ota1:...
```

* Only complete sessions are imported: the recording was stopped properly (`ended_utc` in
  `session.json`) and, for the bucket, the upload finished (`_upload_complete.json`).
* Runs any number of times; already imported files are skipped.
* For a bucket import the club name is the folder in front of the session (the upload code's prefix).
* `--stride 2` evaluates every second frame (twice as fast), `--no-evaluate` only copies.

Archive (`--archive`, default `data/archive`):

```
archive/
  catalog.sqlite / catalog.csv   one row per session (club, member, place, mode, duration,
                                 aircraft, heading correction, max. track range, ...)
  sessions/<club>/<session>/      session folders incl. eval/ (report, overlay, crops)
  dataset/aircraft/               crops of tracks matched to ADS-B/FLARM (auto-labelled)
  dataset/review/                 crops of tracks without cooperative traffic
  dataset/bird|background|unclear/
  labels.csv                      every manual decision with time stamp
```

`catalog.csv` opens directly in Excel (separator `;`). Typical questions it answers: which sessions
had aircraft closer than 3 km, which camera/lens reaches farthest, which members' recordings
have a large heading correction (camera direction entered wrongly).

## 2. Sorting: `ota review`

Shows the crops in `dataset/review/` enlarged, together with the other crops of the same track:

| key | label |
|---|---|
| `1` / `a` | aircraft (non-cooperative: glider without FLARM, ultralight, …) |
| `2` / `b` | bird |
| `3` / `h` | background (cloud edge, reflection, insect, noise) |
| `4` / `u` | unclear (not used for training) |
| Shift + key | label the whole track at once |
| Backspace | undo |
| Space | skip |

Labelling per track is fast: one decision usually covers 10–50 crops.

It is worth spot-checking `dataset/aircraft/` as well. A wrong match (an ADS-B aircraft and a
bird at the same spot) is rare but possible.

## 3. Training set: `ota dataset`

```powershell
ota dataset --out data/crops_own --val 0.2
python tools/train_classifier.py --data data/crops_own
```

The split is **by session**: all crops of one session end up in either train or val. Neighbouring
frames of one track are almost identical, so a random split would make the validation accuracy far
too optimistic.

To combine own crops with the AOT crops, copy both into one folder with the same
`train/val/<class>` structure before training. Own crops are worth more than AOT crops: same
camera, same windscreen, same landscape.
