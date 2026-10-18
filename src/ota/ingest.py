"""Collect, catalogue and evaluate incoming sessions; build the training data set.

Workflow for the project lead::

    ota ingest --from E:\\                     # USB stick / synced cloud folder
    ota ingest --s3 ota1:...                  # bucket, with a key that may READ
    ota review                                # sort the unclear crops by key press
    ota dataset --out data/crops_own          # train/val split for the classifier

Archive layout (``--archive``, default ``data/archive``)::

    archive/
      catalog.sqlite          one row per session (also exported as catalog.csv)
      sessions/<club>/<session>/   the session folders, as recorded
      dataset/
        aircraft/             crops matched to ADS-B/FLARM (auto-labelled) + reviewed
        review/               crops of tracks without cooperative traffic -> ota review
        bird/ background/ unclear/   filled by ota review
      labels.csv              every manual decision (file, label, time)

* Only **complete** sessions are imported: ``session.json`` with an end time, and for
  S3 the ``_upload_complete.json`` manifest. Files already present with the same size
  are skipped, so ingest can run again and again.
* ``--evaluate`` (default on) runs ``ota evaluate`` on every new session and stores the
  key results in the catalogue; the crops go into ``dataset/``.
* ``ota dataset`` splits **by session** (never frames of one session in both train and
  val), otherwise the validation accuracy would be far too optimistic.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import random
import shutil
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

LABELS = ("aircraft", "bird", "background", "unclear")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    name TEXT PRIMARY KEY,
    club TEXT,
    path TEXT,
    operator TEXT,
    station TEXT,
    mode TEXT,
    started_utc TEXT,
    duration_s REAL,
    camera TEXT,
    record_mode TEXT,
    bytes INTEGER,
    aircraft INTEGER,
    notes TEXT,
    ingested_utc TEXT,
    evaluated_utc TEXT,
    eval_error TEXT,
    heading_corr_deg REAL,
    max_track_km REAL,
    max_detection_km REAL,
    matched_tracks INTEGER,
    unmatched_tracks INTEGER
)
"""
COLUMNS = ["name", "club", "path", "operator", "station", "mode", "started_utc", "duration_s",
           "camera", "record_mode", "bytes", "aircraft", "notes", "ingested_utc", "evaluated_utc",
           "eval_error", "heading_corr_deg", "max_track_km", "max_detection_km", "matched_tracks",
           "unmatched_tracks"]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Archive:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "sessions").mkdir(parents=True, exist_ok=True)
        for lab in (*LABELS, "review"):
            (self.root / "dataset" / lab).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "catalog.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute(_SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- catalogue --

    def known(self, name: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM sessions WHERE name = ?", (name,)).fetchone()

    def register(self, folder: Path, club: str) -> None:
        meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        cam = meta.get("camera") or {}
        act = cam.get("actual") or {}
        stats = meta.get("stats") or {}
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        row = {
            "name": folder.name, "club": club, "path": str(folder),
            "operator": meta.get("operator", ""), "station": (meta.get("station") or {}).get("name", ""),
            "mode": meta.get("mode", "ground"), "started_utc": meta.get("started_utc", ""),
            "duration_s": stats.get("duration_s"),
            "camera": f"{act.get('width', '?')}x{act.get('height', '?')} hfov {cam.get('hfov_deg', '?')}"
            if cam else "",
            "record_mode": (meta.get("recording") or {}).get("mode", "all"),
            "bytes": size, "aircraft": (stats.get("traffic") or {}).get("aircraft"),
            "notes": meta.get("notes", ""), "ingested_utc": _now(),
        }
        cols = ", ".join(row)
        self.db.execute(f"INSERT OR REPLACE INTO sessions ({cols}) VALUES "
                        f"({', '.join('?' * len(row))})", list(row.values()))
        self.db.commit()

    def export_csv(self) -> Path:
        out = self.root / "catalog.csv"
        rows = self.db.execute("SELECT * FROM sessions ORDER BY started_utc").fetchall()
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(COLUMNS)
            for r in rows:
                w.writerow([r[c] for c in COLUMNS])
        return out

    # -- evaluation + crops --

    def evaluate(self, name: str, stride: int = 1, config: str | None = None) -> dict | None:
        from .config import Config, load_config
        from .evaluate import SessionEvaluator
        row = self.known(name)
        folder = Path(row["path"])
        meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        if not meta.get("camera"):
            self.db.execute("UPDATE sessions SET evaluated_utc=?, eval_error=? WHERE name=?",
                            (_now(), "no camera", name))
            self.db.commit()
            return None
        try:
            cfg = load_config(config) if config and Path(config).exists() else Config()
            ev = SessionEvaluator(folder, cfg, stride=stride)
            s = ev.run()
            ev.write_outputs(s)
        except Exception as e:
            log.exception("evaluation of %s failed", name)
            self.db.execute("UPDATE sessions SET evaluated_utc=?, eval_error=? WHERE name=?",
                            (_now(), str(e), name))
            self.db.commit()
            return None
        tk = [t["max_track_km"] for t in s["targets"] if t["max_track_km"] is not None]
        dk = [t["max_detection_km"] for t in s["targets"] if t["max_detection_km"] is not None]
        self.db.execute(
            "UPDATE sessions SET evaluated_utc=?, eval_error=NULL, heading_corr_deg=?, max_track_km=?, "
            "max_detection_km=?, matched_tracks=?, unmatched_tracks=? WHERE name=?",
            (_now(), s["camera_correction"]["heading_deg"] if s["camera_correction"]["applied"] else None,
             max(tk) if tk else None, max(dk) if dk else None, s["matched_tracks"],
             len(s["unmatched_tracks"]), name))
        self.db.commit()
        self.collect_crops(folder)
        return s

    def collect_crops(self, folder: Path) -> int:
        """Copy eval/crops/{aircraft,review} into the data set (names carry the session)."""
        n = 0
        for sub in ("aircraft", "review"):
            src = folder / "eval" / "crops" / sub
            if not src.exists():
                continue
            for p in src.glob("*.png"):
                already = any((self.root / "dataset" / lab / p.name).exists()
                              for lab in (*LABELS, "review"))
                if not already:
                    shutil.copy2(p, self.root / "dataset" / sub / p.name)
                    n += 1
        return n

    # -- import --

    def ingest_folder(self, src: str | Path, club: str = "local", evaluate: bool = True,
                      stride: int = 1, move: bool = False) -> list[str]:
        """Import every complete session folder found below ``src``."""
        new = []
        for meta_p in sorted(Path(src).rglob("session.json")):
            folder = meta_p.parent
            if self.root in folder.parents:
                continue
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not meta.get("ended_utc") and meta.get("software") != "synthetic":
                log.info("skipping %s: recording not finished", folder.name)
                continue
            dest = self.root / "sessions" / club / folder.name
            _copy_tree(folder, dest, move=move)
            fresh = self.known(folder.name) is None
            self.register(dest, club)
            if fresh:
                new.append(folder.name)
        if evaluate:
            for name in new:
                self.evaluate(name, stride=stride)
        self.export_csv()
        return new

    def ingest_s3(self, cfg, evaluate: bool = True, stride: int = 1) -> list[str]:
        """Download complete sessions (with _upload_complete.json) from S3-compatible storage."""
        from .recorder.upload import COMPLETE_FILE
        c = cfg.client()
        prefix = (cfg.prefix.strip("/") + "/") if cfg.prefix.strip("/") else ""
        manifests = []
        token = None
        while True:
            kw = {"Bucket": cfg.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            r = c.list_objects_v2(**kw)
            manifests += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith("/" + COMPLETE_FILE)]
            if not r.get("IsTruncated"):
                break
            token = r["NextContinuationToken"]
        new = []
        for mkey in manifests:
            base = mkey[: -len(COMPLETE_FILE) - 1]
            parts = base.split("/")
            name = parts[-1]
            club = parts[-2] if len(parts) >= 2 else "s3"
            manifest = json.loads(c.get_object(Bucket=cfg.bucket, Key=mkey)["Body"].read())
            dest = self.root / "sessions" / club / name
            for rel, size in manifest["files"].items():
                target = dest / rel
                if target.exists() and target.stat().st_size == size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".part")
                c.download_file(cfg.bucket, f"{base}/{rel}", str(tmp))
                tmp.replace(target)
            fresh = self.known(name) is None
            self.register(dest, club)
            if fresh:
                new.append(name)
        if evaluate:
            for name in new:
                self.evaluate(name, stride=stride)
        self.export_csv()
        return new

    # -- labels / data set --

    def review_items(self) -> list[Path]:
        return sorted((self.root / "dataset" / "review").glob("*.png"))

    def label(self, crop: Path, label: str) -> Path:
        """Move a crop into dataset/<label>/ and log the decision. Returns the new path."""
        if label not in LABELS:
            raise ValueError(label)
        dest = self.root / "dataset" / label / crop.name
        shutil.move(str(crop), dest)
        with open(self.root / "labels.csv", "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([crop.name, label, _now()])
        return dest

    def unlabel(self, crop: Path) -> Path:
        """Undo: move a crop back into review/."""
        dest = self.root / "dataset" / "review" / crop.name
        shutil.move(str(crop), dest)
        with open(self.root / "labels.csv", "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([crop.name, "undo", _now()])
        return dest

    def export_dataset(self, out: str | Path, val_fraction: float = 0.2, seed: int = 1) -> dict:
        """Write out/{train,val}/{aircraft,bird,background}/ for the classifier, split by session."""
        out = Path(out)
        classes = ("aircraft", "bird", "background")
        files = {c: sorted((self.root / "dataset" / c).glob("*.png")) for c in classes}
        sessions = sorted({session_of(p.name) for fs in files.values() for p in fs})
        rnd = random.Random(seed)
        rnd.shuffle(sessions)
        n_val = max(1, round(len(sessions) * val_fraction)) if len(sessions) > 1 else 0
        val_sessions = set(sessions[:n_val])
        counts: dict = {"train": {}, "val": {}, "val_sessions": sorted(val_sessions)}
        for c, fs in files.items():
            for split in ("train", "val"):
                (out / split / c).mkdir(parents=True, exist_ok=True)
            for p in fs:
                split = "val" if session_of(p.name) in val_sessions else "train"
                shutil.copy2(p, out / split / c / p.name)
                counts[split][c] = counts[split].get(c, 0) + 1
        return counts


def session_of(crop_name: str) -> str:
    """Crop names start with the session name: <yyyymmdd-hhmmssZ_STATION>_<...>."""
    parts = crop_name.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else crop_name


def _copy_tree(src: Path, dest: Path, move: bool = False) -> None:
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        target = dest / p.relative_to(src)
        if target.exists() and target.stat().st_size == p.stat().st_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(p), target)
        else:
            shutil.copy2(p, target)
