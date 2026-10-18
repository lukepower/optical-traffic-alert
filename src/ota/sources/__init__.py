"""Frame sources.

All sources yield ``Frame`` objects. The pipeline does not care whether the
pixels come from a file, a USB webcam or (later) a GMSL2 camera on a Jetson.

Source strings (``CameraConfig.source``):

* ``file:path/to/video.mp4`` - recorded video, timestamps from the file's fps
* ``usb:0``                  - OpenCV camera index (DirectShow on Windows)
* ``gmsl:0``                 - Jetson GMSL2 camera via GStreamer (not yet implemented)
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from ..config import CameraConfig


@dataclass
class Frame:
    camera: str
    index: int
    t: float          # seconds, monotonic within one source
    image: np.ndarray  # BGR


class FrameSource:
    def __init__(self, cam: CameraConfig):
        self.cam = cam

    def frames(self) -> Iterator[Frame]:
        raise NotImplementedError

    @property
    def size(self) -> tuple[int, int]:
        return self.cam.width, self.cam.height

    def close(self) -> None:
        pass


class VideoFileSource(FrameSource):
    """Plays back a recorded video. ``realtime=True`` throttles to the file's fps."""

    def __init__(self, cam: CameraConfig, path: str, realtime: bool = False, loop: bool = False):
        super().__init__(cam)
        self.path = path
        self.realtime = realtime
        self.loop = loop
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise FileNotFoundError(f"Cannot open video: {path}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or cam.fps
        self._w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    @property
    def size(self) -> tuple[int, int]:
        return self._w, self._h

    def frames(self) -> Iterator[Frame]:
        i = 0
        start = time.monotonic()
        while True:
            ok, img = self.cap.read()
            if not ok:
                if self.loop:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break
            t = i / self.fps
            if self.realtime:
                delay = start + t - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            yield Frame(self.cam.name, i, t, img)
            i += 1

    def close(self) -> None:
        self.cap.release()


class UsbCameraSource(FrameSource):
    """Live camera through OpenCV (UVC webcams, e.g. the IMX678 USB module)."""

    def __init__(self, cam: CameraConfig, index: int):
        super().__init__(cam)
        backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, backend)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera {index}")
        # Many USB2 cameras only deliver high resolutions as MJPEG
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam.height)
        self.cap.set(cv2.CAP_PROP_FPS, cam.fps)
        self._w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    @property
    def size(self) -> tuple[int, int]:
        return self._w, self._h

    def frames(self) -> Iterator[Frame]:
        i = 0
        t0 = time.monotonic()
        while True:
            ok, img = self.cap.read()
            if not ok:
                break
            yield Frame(self.cam.name, i, time.monotonic() - t0, img)
            i += 1

    def close(self) -> None:
        self.cap.release()


def open_source(cam: CameraConfig, realtime: bool = False, loop: bool = False) -> FrameSource:
    kind, _, arg = cam.source.partition(":")
    if kind == "file":
        return VideoFileSource(cam, arg, realtime=realtime, loop=loop)
    if kind == "usb":
        return UsbCameraSource(cam, int(arg or 0))
    if kind == "gmsl":
        raise NotImplementedError(
            "GMSL2 cameras are planned for the Jetson phase. "
            "They will be read through a GStreamer pipeline (nvarguscamerasrc / v4l2src)."
        )
    raise ValueError(f"Unknown source type: {cam.source!r}")
