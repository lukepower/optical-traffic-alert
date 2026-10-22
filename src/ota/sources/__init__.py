"""Frame sources.

All sources yield ``Frame`` objects. The pipeline does not care whether the
pixels come from a file, a USB webcam or (later) a GMSL2 camera on a Jetson.

Source strings (``CameraConfig.source``):

* ``file:path/to/video.mp4`` - recorded video, timestamps from the file's fps
* ``usb:0``                  - OpenCV camera index (DirectShow on Windows)
* ``gmsl:0``                 - Jetson GMSL2 camera via GStreamer (not yet implemented)
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from ..config import CameraConfig

log = logging.getLogger(__name__)


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


def fourcc_str(value: float) -> str:
    """FOURCC code as text; drivers sometimes report garbage -> shown as hex."""
    v = int(value) & 0xFFFFFFFF
    s = "".join(chr((v >> 8 * i) & 0xFF) for i in range(4)).strip("\x00")
    return s if s and all(32 <= ord(c) < 127 for c in s) else (f"0x{v:08X}" if v else "?")


def open_usb_camera(index: int, width: int, height: int, fps: float, pixel_format: str = "YUY2",
                    exposure: float | None = None, gain: float | None = None) -> cv2.VideoCapture:
    """Open a UVC camera and request format, size and frame rate.

    The camera may silently choose something else - always check the
    returned capture's CAP_PROP_* values (``ota probe`` does that)."""
    backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {index}")
    if pixel_format:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*pixel_format[:4].ljust(4)))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    if exposure is not None:
        # DirectShow: 0.25 = manual; V4L2: 1 = manual
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if platform.system() == "Windows" else 1)
        cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
    if gain is not None:
        cap.set(cv2.CAP_PROP_GAIN, gain)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class UsbCameraSource(FrameSource):
    """Live UVC camera (e.g. Svpro IMX415 USB 3.0, IMX678 modules).

    With ``latest_only=True`` a background thread keeps grabbing and the
    pipeline always gets the newest frame. If processing is slower than the
    camera, old frames are dropped instead of piling up - essential for a
    warning system, where a 2-second-old frame is useless.
    """

    def __init__(self, cam: CameraConfig, index: int, latest_only: bool = True):
        super().__init__(cam)
        self.cap = open_usb_camera(index, cam.width, cam.height, cam.fps, cam.pixel_format,
                                   cam.exposure, cam.gain)
        self._w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.format = fourcc_str(self.cap.get(cv2.CAP_PROP_FOURCC))
        self.latest_only = latest_only
        self._lock = threading.Lock()
        self._latest: tuple[float, np.ndarray] | None = None
        self._stop = threading.Event()
        self.dropped = 0
        if (self._w, self._h) != (cam.width, cam.height):
            log.warning("camera %s delivers %dx%d instead of %dx%d", cam.name, self._w, self._h,
                        cam.width, cam.height)
        if latest_only:
            threading.Thread(target=self._grab, daemon=True).start()

    def _grab(self) -> None:
        t0 = time.monotonic()
        while not self._stop.is_set():
            ok, img = self.cap.read()
            if not ok:
                break
            with self._lock:
                if self._latest is not None:
                    self.dropped += 1
                self._latest = (time.monotonic() - t0, img)
        self._stop.set()

    @property
    def size(self) -> tuple[int, int]:
        return self._w, self._h

    def frames(self) -> Iterator[Frame]:
        i = 0
        t0 = time.monotonic()
        while True:
            if self.latest_only:
                item = None
                while item is None:
                    if self._stop.is_set() and self._latest is None:
                        return
                    with self._lock:
                        item, self._latest = self._latest, None
                    if item is None:
                        time.sleep(0.002)
                t, img = item
            else:
                ok, img = self.cap.read()
                if not ok:
                    return
                t = time.monotonic() - t0
            yield Frame(self.cam.name, i, t, img)
            i += 1

    def close(self) -> None:
        self._stop.set()
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
