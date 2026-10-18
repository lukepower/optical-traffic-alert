"""``ota review`` - sort unclear crops by key press.

Shows the crops in ``archive/dataset/review/`` (tracks without a cooperative
aircraft) one at a time, enlarged, together with the other crops of the same
track. Keys:

======================  ==============================================
``1`` / ``a``           aircraft
``2`` / ``b``           bird
``3`` / ``h``           background (cloud edge, reflection, noise ...)
``4`` / ``u``           unclear (kept out of training)
Shift + key             apply to *all* remaining crops of this track
``Backspace``           undo the last decision
``Right`` / ``Space``   skip
``Esc``                 quit
======================  ==============================================
"""

from __future__ import annotations

import base64
import re
import tkinter as tk
from pathlib import Path

import cv2

from .ingest import Archive

KEYS = {"1": "aircraft", "a": "aircraft", "2": "bird", "b": "bird",
        "3": "background", "h": "background", "4": "unclear", "u": "unclear"}
SHIFT = {"exclam": "1", "quotedbl": "2", "at": "2", "section": "3", "numbersign": "3",
         "dollar": "4", "A": "a", "B": "b", "H": "h", "U": "u"}

_TRACK = re.compile(r"^(.*_trk\d+)_")


def track_of(name: str) -> str:
    m = _TRACK.match(name)
    return m.group(1) if m else name


class ReviewApp:
    SCALE = 4

    def __init__(self, root: tk.Tk, archive: Archive):
        self.root, self.ar = root, archive
        self.items = archive.review_items()
        self.pos = 0
        self.history: list[tuple[list[Path], list[Path]]] = []  # (new paths, old paths)
        self.done = 0
        root.title("OTA review")
        self.info = tk.StringVar()
        tk.Label(root, textvariable=self.info, font=("Segoe UI", 11), anchor="w",
                 justify="left").pack(fill="x", padx=8, pady=4)
        self.canvas = tk.Canvas(root, width=64 * self.SCALE, height=64 * self.SCALE, bg="#222")
        self.canvas.pack(padx=8)
        self.strip = tk.Frame(root)
        self.strip.pack(padx=8, pady=6)
        tk.Label(root, text="1/a aircraft   2/b bird   3/h background   4/u unclear\n"
                            "Shift + key = whole track   Backspace = undo   Space = skip   Esc = quit",
                 fg="#555").pack(padx=8, pady=(0, 8))
        root.bind("<Key>", self.on_key)
        self._photos: list = []
        self.show()

    def current(self) -> Path | None:
        while self.pos < len(self.items) and not self.items[self.pos].exists():
            self.pos += 1
        return self.items[self.pos] if self.pos < len(self.items) else None

    def _photo(self, path: Path, scale: int):
        img = cv2.imread(str(path))
        if img is None:
            return None
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        ok, png = cv2.imencode(".png", img)
        return tk.PhotoImage(data=base64.b64encode(png.tobytes()).decode()) if ok else None

    def show(self) -> None:
        cur = self.current()
        self.canvas.delete("all")
        for w in self.strip.winfo_children():
            w.destroy()
        self._photos = []
        if cur is None:
            self.info.set(f"Done – {self.done} labelled. Nothing left to review.")
            return
        same = [p for p in self.items[self.pos:] if p.exists() and track_of(p.name) == track_of(cur.name)]
        self.info.set(f"{self.pos + 1} / {len(self.items)}   ({self.done} labelled)\n{cur.name}\n"
                      f"this track: {len(same)} crops")
        ph = self._photo(cur, self.SCALE)
        if ph:
            self._photos.append(ph)
            self.canvas.create_image(32 * self.SCALE, 32 * self.SCALE, image=ph)
        for p in same[1:9]:
            t = self._photo(p, 1)
            if t:
                self._photos.append(t)
                tk.Label(self.strip, image=t, bd=1, relief="solid").pack(side="left", padx=2)

    def decide(self, label: str, whole_track: bool) -> None:
        cur = self.current()
        if cur is None:
            return
        targets = [cur]
        if whole_track:
            targets = [p for p in self.items[self.pos:]
                       if p.exists() and track_of(p.name) == track_of(cur.name)]
        new = [self.ar.label(p, label) for p in targets]
        self.history.append((new, targets))
        self.done += len(new)
        self.pos += 1
        self.show()

    def undo(self) -> None:
        if not self.history:
            return
        new, old = self.history.pop()
        for p in new:
            self.ar.unlabel(p)
        self.done -= len(new)
        self.pos = min(self.items.index(o) for o in old)
        self.show()

    def on_key(self, e: tk.Event) -> None:
        k = e.keysym
        shift = bool(e.state & 0x1) or k in SHIFT
        k = SHIFT.get(k, k).lower() if len(SHIFT.get(k, k)) == 1 else k
        if k in KEYS:
            self.decide(KEYS[k], shift)
        elif k == "BackSpace":
            self.undo()
        elif k in ("Right", "space"):
            self.pos += 1
            self.show()
        elif k == "Escape":
            self.root.destroy()


def main(archive_dir: str) -> int:
    root = tk.Tk()
    ar = Archive(archive_dir)
    ReviewApp(root, ar)
    root.mainloop()
    ar.close()
    return 0
