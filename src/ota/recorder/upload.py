"""Handing in sessions: upload to S3-compatible storage (Backblaze B2, Cloudflare R2, ...) or copy to a drive.

Upload
------
The project lead creates a bucket and a **write-only** key and turns them into
an *upload code* (``ota upload-code ...``). Club members paste the code into
the recorder once; afterwards a session is handed in with one click.

* Every file of the session folder becomes one object:
  ``<prefix>/<session name>/<relative path>``.
* Large files use multipart uploads (32 MB parts). Progress is saved in
  ``upload_state.json`` inside the session folder after every part, so an
  interrupted upload (laptop closed, network gone) continues where it stopped.
* When all files are uploaded, ``_upload_complete.json`` (file list and sizes)
  is written last. Its presence tells the receiver that the session is complete.

Recommended: **Backblaze B2** with an application key restricted to the bucket
with only ``writeFiles`` (plus ``listAllBucketNames``). Such a key can add
files but cannot read, list or delete anything, so a leaked code cannot expose
other members' videos. Cloudflare R2 tokens always include read access, so an
R2 code should only go to people you would also give the recordings to (or put
a small presigned-URL service in front, see docs/recorder.md).

Copy
----
``copy_session`` copies a session folder to another drive (USB stick, a
synced cloud folder such as Google Drive / OneDrive / Dropbox) and verifies
the file sizes.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

CODE_PREFIX = "ota1:"
PART_SIZE = 32 * 1024 * 1024
STATE_FILE = "upload_state.json"
COMPLETE_FILE = "_upload_complete.json"
SKIP = {STATE_FILE}


@dataclass
class UploadConfig:
    endpoint: str        # e.g. https://s3.eu-central-003.backblazeb2.com
    bucket: str
    key_id: str
    secret: str
    region: str = "auto"
    prefix: str = ""     # e.g. "aeroclub-bozen"

    def to_code(self) -> str:
        raw = json.dumps(asdict(self), separators=(",", ":")).encode()
        return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def from_code(cls, code: str) -> "UploadConfig":
        code = "".join(code.split())
        if not code.startswith(CODE_PREFIX):
            raise ValueError("not an OTA upload code (must start with 'ota1:')")
        b = code[len(CODE_PREFIX):]
        d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def describe(self) -> str:
        host = self.endpoint.split("//")[-1].split("/")[0]
        return f"{self.bucket}/{self.prefix} @ {host}".replace("//", "/")

    def client(self):
        import boto3  # pip install boto3
        from botocore.config import Config
        return boto3.client(
            "s3", endpoint_url=self.endpoint, region_name=self.region or "auto",
            aws_access_key_id=self.key_id, aws_secret_access_key=self.secret,
            config=Config(retries={"max_attempts": 5, "mode": "standard"},
                          connect_timeout=20, read_timeout=120,
                          # plain checksums only: B2/R2 do not support all new AWS ones
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required"))


def session_files(folder: Path) -> list[tuple[str, int]]:
    out = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.name not in SKIP and p.name != COMPLETE_FILE:
            out.append((p.relative_to(folder).as_posix(), p.stat().st_size))
    return out


def upload_status(folder: Path) -> str:
    """'' (never), 'partial', 'done'."""
    try:
        st = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return "done" if st.get("complete") else "partial"


@dataclass
class Progress:
    done_bytes: int = 0
    total_bytes: int = 0
    current: str = ""
    rate_bps: float = 0.0
    finished: bool = False
    error: str = ""

    @property
    def fraction(self) -> float:
        return self.done_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def eta_s(self) -> float | None:
        if self.rate_bps <= 0:
            return None
        return (self.total_bytes - self.done_bytes) / self.rate_bps


class SessionUploader:
    """Resumable upload of one session folder. Run ``run()`` in a thread; ``stop()`` pauses."""

    def __init__(self, folder: str | Path, cfg: UploadConfig, part_size: int = PART_SIZE,
                 on_progress: Callable[[Progress], None] | None = None):
        self.folder = Path(folder)
        self.cfg = cfg
        self.part_size = part_size
        self.on_progress = on_progress
        self.progress = Progress()
        self._stop = threading.Event()
        self._client = None
        self.base = "/".join(x for x in (cfg.prefix.strip("/"), self.folder.name) if x)

    def stop(self) -> None:
        self._stop.set()

    # -- state --

    def _load_state(self) -> dict:
        target = f"{self.cfg.endpoint}/{self.cfg.bucket}/{self.base}"
        try:
            st = json.loads((self.folder / STATE_FILE).read_text(encoding="utf-8"))
            if st.get("target") == target:
                return st
        except (OSError, ValueError):
            pass
        return {"target": target, "files": {}, "complete": False}

    def _save_state(self, st: dict) -> None:
        tmp = self.folder / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        os.replace(tmp, self.folder / STATE_FILE)

    # -- upload --

    def run(self) -> bool:
        """Upload everything not yet uploaded. Returns True when the session is complete."""
        pr = self.progress
        try:
            self._client = self.cfg.client()
            st = self._load_state()
            files = session_files(self.folder)
            pr.total_bytes = sum(size for _, size in files)
            pr.done_bytes = 0
            for rel, size in files:
                fs = st["files"].get(rel)
                if fs and fs.get("done") and fs.get("size") == size:
                    pr.done_bytes += size
                elif fs and fs.get("size") == size and fs.get("parts"):
                    pr.done_bytes += sum(p["size"] for p in fs["parts"])
            self._t0, self._b0 = time.monotonic(), pr.done_bytes
            for rel, size in files:
                if self._stop.is_set():
                    return False
                fs = st["files"].get(rel)
                if fs and fs.get("done") and fs.get("size") == size:
                    continue
                if not fs or fs.get("size") != size:
                    fs = st["files"][rel] = {"size": size, "done": False}
                pr.current = rel
                self._emit()
                if size <= self.part_size:
                    self._put_small(rel, size)
                else:
                    try:
                        ok = self._put_multipart(rel, size, fs, st)
                    except Exception as e:
                        if "NoSuchUpload" not in str(e):
                            raise
                        # the unfinished upload expired on the server: start this file again
                        log.warning("multipart upload of %s expired, restarting the file", rel)
                        pr.done_bytes -= sum(p["size"] for p in fs.get("parts", []))
                        fs.pop("upload_id", None)
                        fs["parts"] = []
                        ok = self._put_multipart(rel, size, fs, st)
                    if not ok:
                        return False
                fs["done"] = True
                fs.pop("parts", None)
                fs.pop("upload_id", None)
                self._save_state(st)
            manifest = {"session": self.folder.name, "uploaded_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": dict(files),
                "total_bytes": pr.total_bytes}
            self._client.put_object(Bucket=self.cfg.bucket, Key=f"{self.base}/{COMPLETE_FILE}",
                                    Body=json.dumps(manifest, indent=1).encode())
            st["complete"] = True
            self._save_state(st)
            pr.finished = True
            pr.current = ""
            self._emit()
            return True
        except Exception as e:
            pr.error = str(e) or e.__class__.__name__
            log.exception("upload of %s failed", self.folder.name)
            self._emit()
            return False

    def _emit(self) -> None:
        pr = self.progress
        dt = time.monotonic() - getattr(self, "_t0", time.monotonic())
        if dt > 1:
            pr.rate_bps = (pr.done_bytes - self._b0) / dt
        if self.on_progress:
            self.on_progress(pr)

    def _put_small(self, rel: str, size: int) -> None:
        with open(self.folder / rel, "rb") as f:
            self._client.put_object(Bucket=self.cfg.bucket, Key=f"{self.base}/{rel}", Body=f.read())
        self.progress.done_bytes += size
        self._emit()

    def _put_multipart(self, rel: str, size: int, fs: dict, st: dict) -> bool:
        c, key = self._client, f"{self.base}/{rel}"
        if not fs.get("upload_id"):
            fs["upload_id"] = c.create_multipart_upload(Bucket=self.cfg.bucket, Key=key)["UploadId"]
            fs["parts"] = []
            self._save_state(st)
        parts = fs["parts"]
        n_parts = (size + self.part_size - 1) // self.part_size
        with open(self.folder / rel, "rb") as f:
            for n in range(len(parts) + 1, n_parts + 1):
                if self._stop.is_set():
                    return False
                f.seek((n - 1) * self.part_size)
                data = f.read(self.part_size)
                r = c.upload_part(Bucket=self.cfg.bucket, Key=key, UploadId=fs["upload_id"],
                                  PartNumber=n, Body=data)
                parts.append({"n": n, "etag": r["ETag"], "size": len(data)})
                self._save_state(st)
                self.progress.done_bytes += len(data)
                self._emit()
        c.complete_multipart_upload(
            Bucket=self.cfg.bucket, Key=key, UploadId=fs["upload_id"],
            MultipartUpload={"Parts": [{"PartNumber": p["n"], "ETag": p["etag"]} for p in parts]})
        return True


def copy_session(folder: str | Path, dest_parent: str | Path,
                 on_progress: Callable[[Progress], None] | None = None,
                 stop: threading.Event | None = None) -> Path:
    """Copy a session folder into ``dest_parent`` (USB stick, synced cloud folder) and verify sizes."""
    folder, dest = Path(folder), Path(dest_parent) / Path(folder).name
    files = session_files(folder)
    pr = Progress(total_bytes=sum(s for _, s in files))
    free = shutil.disk_usage(dest_parent).free
    if free < pr.total_bytes:
        raise OSError(f"not enough space on {dest_parent}: {free / 1e9:.1f} GB free, "
                      f"{pr.total_bytes / 1e9:.1f} GB needed")
    t0 = time.monotonic()
    for rel, size in files:
        src, dst = folder / rel, dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size == size:
            pr.done_bytes += size
            continue
        pr.current = rel
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                if stop is not None and stop.is_set():
                    raise InterruptedError("copy cancelled")
                buf = fi.read(8 * 1024 * 1024)
                if not buf:
                    break
                fo.write(buf)
                pr.done_bytes += len(buf)
                pr.rate_bps = pr.done_bytes / max(time.monotonic() - t0, 1e-3)
                if on_progress:
                    on_progress(pr)
        if dst.stat().st_size != size:
            raise OSError(f"size mismatch after copying {rel}")
    pr.finished = True
    if on_progress:
        on_progress(pr)
    return dest


def list_sessions(parent: str | Path) -> list[dict]:
    """Sessions in the output folder, newest first, with size, duration and upload status."""
    out = []
    parent = Path(os.path.expanduser(str(parent)))
    if not parent.exists():
        return out
    for d in parent.iterdir():
        meta_p = d / "session.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        size = sum(s for _, s in session_files(d))
        out.append({"path": d, "name": d.name, "bytes": size,
                    "duration_s": (meta.get("stats") or {}).get("duration_s", 0),
                    "running": meta.get("ended_utc") is None,
                    "upload": upload_status(d)})
    out.sort(key=lambda x: x["name"], reverse=True)
    return out
