"""Video writers for raw recordings (shared by ``ota record`` and the session recorder)."""

from __future__ import annotations

from pathlib import Path

CODECS = {
    # name: (fourcc, extension, extra VideoWriter params)
    "mjpg": ("MJPG", ".avi", [1, 95]),   # VIDEOWRITER_PROP_QUALITY = 1, 95 %: near-lossless, cheap
    "ffv1": ("FFV1", ".mkv", []),        # lossless, large files, more CPU
    "mp4v": ("mp4v", ".mp4", []),        # small files, but blurs tiny targets - not for training data
}


def open_writer(path: str | Path, codec: str, fps: float, size: tuple[int, int]):
    """Open a cv2.VideoWriter; returns (writer, actual path with the codec's extension)."""
    import cv2
    fourcc, ext, params = CODECS[codec]
    p = Path(path)
    if p.suffix.lower() != ext:
        p = p.with_suffix(ext)
    p.parent.mkdir(parents=True, exist_ok=True)
    if codec == "mjpg":
        # OpenCV's built-in MJPEG writer supports the quality parameter
        w = cv2.VideoWriter(str(p), cv2.CAP_OPENCV_MJPEG, cv2.VideoWriter_fourcc(*fourcc), fps,
                            size, params)
    else:
        w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*fourcc), fps, size, params)
    if not w.isOpened():
        raise RuntimeError(f"cannot open video writer {p} with codec {codec}")
    return w, p
